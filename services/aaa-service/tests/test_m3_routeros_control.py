"""M3 RouterOS control: readiness statuses, typed operations allowlist,
prohibited operations, Winbox setup guide."""
import pytest

from app.network_control.routeros_control import (
    ProhibitedOperationError,
    RouterOSControl,
    build_winbox_guide,
    run_readiness_check,
)
from app.routeros import FakeRouterOSAdapter, RouterOSAuthenticationError


def _ready_adapter() -> FakeRouterOSAdapter:
    adapter = FakeRouterOSAdapter(version="7.15")
    adapter.seed_radius_entry(address="10.50.0.10", service=["pppoe"])
    adapter.radius_incoming[0]["accept"] = True
    adapter.ppp_aaa["use_radius"] = True
    adapter.ppp_aaa["accounting"] = True
    return adapter


def test_readiness_ready():
    report = run_readiness_check(_ready_adapter())
    assert report["status"] in ("READY", "READY_WITH_WARNINGS")
    assert report["checks"]["reachable"]["ok"] is True
    assert "steps" in report["winbox_guide"]


def test_readiness_missing_configuration():
    adapter = FakeRouterOSAdapter()  # no radius entries, no incoming, no aaa
    report = run_readiness_check(adapter)
    assert report["status"] == "MISSING_CONFIGURATION"
    steps = " ".join(report["winbox_guide"]["steps"])
    assert "RADIUS client" in steps
    assert "RADIUS incoming" in steps
    assert "PPP AAA" in steps


def test_readiness_authentication_failed():
    adapter = FakeRouterOSAdapter()
    adapter.fail_auth = True
    report = run_readiness_check(adapter)
    assert report["status"] == "AUTHENTICATION_FAILED"


def test_readiness_unreachable():
    adapter = FakeRouterOSAdapter()

    class _Broken(FakeRouterOSAdapter):
        def test_connection(self):
            from app.routeros import RouterOSTimeoutError

            raise RouterOSTimeoutError(code="TIMEOUT")

    report = run_readiness_check(_Broken())
    assert report["status"] == "UNREACHABLE"


def test_winbox_guide_has_no_secrets():
    guide = build_winbox_guide(["RADIUS client entry"], ["PPP AAA interim update"], nas=None)
    text = " ".join(guide["steps"])
    assert "secret" not in text.lower() or "shared secret" not in text.lower()
    assert "password" not in text.lower()


def test_typed_operations_allowlist():
    adapter = _ready_adapter()
    control = RouterOSControl(adapter)
    assert control.call("read_queue_types") == []
    adapter.seed_queue_type(name="qt-voice", comment="managed-by=isp-platform")
    assert control.call("read_queue_types")[0]["name"] == "qt-voice"
    remote_id = control.call("create_managed_queue_type", {"name": "qt-video", "kind": "pcq", "comment": "managed-by=isp-platform"})
    assert len(control.call("read_queue_types")) == 2
    control.call("remove_managed_object", "queue_type", remote_id)
    assert len(control.call("read_queue_types")) == 1  # seeded one remains


def test_prohibited_operations_rejected():
    adapter = FakeRouterOSAdapter()
    control = RouterOSControl(adapter)
    for operation in ("reboot", "factory_reset", "run_script", "console_command", "firewall_replace"):
        with pytest.raises(ProhibitedOperationError):
            control.call(operation)


def test_unknown_operation_rejected():
    control = RouterOSControl(FakeRouterOSAdapter())
    with pytest.raises(ProhibitedOperationError):
        control.call("not_an_operation")


def test_managed_object_diff_and_verify(session, tenant, nas):
    adapter = _ready_adapter()
    control = RouterOSControl(adapter)
    desired = [{"kind": "queue_type", "name": "qt-voice", "params": {"name": "qt-voice", "kind": "pcq", "comment": "managed-by=isp-platform"}, "tags": {"managed": True}}]
    observed = control.call("read_queue_types")
    desired_keys = {f"{o['kind']}:{o['params']['name']}" for o in desired}
    present = {f"queue_type:{i['name']}" for i in observed}
    assert bool(desired_keys - present)  # missing initially
    control.call("create_managed_queue_type", desired[0]["params"])
    present = {f"queue_type:{i['name']}" for i in control.call("read_queue_types")}
    assert not (desired_keys - present)
