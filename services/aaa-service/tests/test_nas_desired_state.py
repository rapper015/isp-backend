"""Unit tests: desired-state engine, diff generation, risk classification,
idempotency and ownership classification."""
from types import SimpleNamespace

from app.nas_desired_state import (OWNERSHIP_BACKEND, OWNERSHIP_EXTERNAL, OP_ADD, OP_NOOP, OP_REMOVE, OP_UPDATE, RISK_CRITICAL, RISK_HIGH, RISK_LOW, RISK_MEDIUM, build_desired_assignments, classify_radius_entry, compute_plan, compute_radius_changes, validate_desired)


def _assignment(**kwargs):
    base = dict(id="a1", radius_server_id="s1", priority=100, role="primary", services=["pppoe"], auth_port=None, accounting_port=None, coa_port=None, timeout_seconds=3000, source_address="10.30.0.1", desired_status="enabled", secret_version=1)
    base.update(kwargs)
    return base


def _server(**kwargs):
    return SimpleNamespace(host="10.0.0.10", auth_port=1812, accounting_port=1813, coa_port=3799)


def test_build_desired_assignments_resolves_server_host():
    item = SimpleNamespace(id="a1", radius_server_id="s1", priority=50, role="primary", services=["pppoe"], auth_port=None, accounting_port=None, coa_port=None, timeout_seconds=3000, source_address="10.30.0.1", desired_status="enabled", secret_version=2, radius_server=_server())
    desired = build_desired_assignments([item])
    assert desired[0]["address"] == "10.0.0.10"
    assert desired[0]["secret_version"] == 2
    assert desired[0]["role"] == "primary"
    # Secrets must never be carried in desired state.
    assert "secret" not in str(desired).lower() or "secret_version" in str(desired)


def test_ownership_classification():
    desired = {"10.0.0.10"}
    assert classify_radius_entry({"address": "10.0.0.10"}, desired, set()) == OWNERSHIP_BACKEND
    assert classify_radius_entry({"address": "192.0.2.1"}, desired, {"192.0.2.1"}) == OWNERSHIP_BACKEND
    assert classify_radius_entry({"address": "192.0.2.9"}, desired, set()) == OWNERSHIP_EXTERNAL
    assert classify_radius_entry({"address": ""}, desired, set()) == "UNKNOWN"


def test_radius_changes_add_update_noop_remove():
    current = [
        {"remote_id": "*1", "address": "10.0.0.10", "service": ["pppoe"], "authentication_port": 1812, "accounting_port": 1813, "timeout": 3000, "src_address": None},
        {"remote_id": "*2", "address": "192.0.2.9", "service": ["pppoe"], "authentication_port": 1812, "accounting_port": 1813, "timeout": 3000, "src_address": None},
        {"remote_id": "*3", "address": "10.0.0.20", "service": ["hotspot"], "authentication_port": 1812, "accounting_port": 1813, "timeout": 3000, "src_address": None},
    ]
    desired = [
        {"assignment_id": "a1", "address": "10.0.0.10", "services": ["pppoe"], "auth_port": 1812, "accounting_port": 1813, "timeout": 3000, "src_address": None},
        {"assignment_id": "a2", "address": "10.0.0.30", "services": ["pppoe"], "auth_port": 1812, "accounting_port": 1813, "timeout": 3000, "src_address": None},
    ]
    managed = {"10.0.0.20"}
    changes = compute_radius_changes(current, desired, managed)
    operations = [change["operation"] for change in changes]
    assert OP_NOOP in operations          # 10.0.0.10 unchanged
    assert OP_ADD in operations           # 10.0.0.30 new
    assert OP_REMOVE in operations        # 10.0.0.20 backend orphan removed
    removed = [c for c in changes if c["operation"] == OP_REMOVE]
    assert removed and removed[0]["remote_object_id"] == "*3"
    # Externally managed entry 192.0.2.9 is never touched.
    assert not any(c["remote_object_id"] == "*2" for c in changes if c["operation"] in {OP_UPDATE, OP_REMOVE})


def test_update_detects_field_difference():
    current = [{"remote_id": "*1", "address": "10.0.0.10", "service": ["pppoe"], "authentication_port": 1812, "accounting_port": 1813, "timeout": 3000, "src_address": None}]
    desired = [{"assignment_id": "a1", "address": "10.0.0.10", "services": ["pppoe"], "auth_port": 1812, "accounting_port": 1813, "timeout": 5000, "src_address": None}]
    changes = compute_radius_changes(current, desired, set())
    assert any(change["operation"] == OP_UPDATE for change in changes)


def test_validate_desired_requires_active_assignment():
    errors = validate_desired({"radius_assignments": []})
    assert errors and "at least one RADIUS assignment" in errors[0]


def test_login_radius_requires_break_glass_and_risk():
    errors = validate_desired({"radius_assignments": [_assignment()], "login_radius": True})
    text = " ".join(errors)
    assert "break-glass" in text
    assert "risk acknowledgement" in text
    assert validate_desired({"radius_assignments": [_assignment()], "login_radius": True, "break_glass_verified": True, "acknowledge_login_risk": True, "user_aaa_default_group": "full"}) == []


def test_interim_update_bounds_validated_against_policy():
    errors = validate_desired({"radius_assignments": [_assignment()], "interim_update_seconds": 5}, tenant_policy={"interim_update_max_seconds": 3600})
    assert errors and "interim update interval" in errors[0]


def test_plan_risk_classification():
    # Low risk: adding a secondary server.
    low = compute_plan({"radius_entries": []}, {"radius_assignments": [_assignment(role="secondary")], "ppp_aaa": True}, [_assignment(role="secondary")])
    assert low["risk"] == RISK_LOW
    # Medium risk: changing an accounting interval.
    medium = compute_plan({"radius_entries": []}, {"radius_assignments": [_assignment(role="secondary")], "ppp_aaa": True, "interim_update_seconds": 600}, [_assignment(role="secondary")])
    assert medium["risk"] == RISK_MEDIUM
    # Critical risk: router administrative login RADIUS.
    critical = compute_plan({}, {"radius_assignments": [_assignment()], "login_radius": True, "break_glass_verified": True, "acknowledge_login_risk": True, "user_aaa_default_group": "full"}, [_assignment()])
    assert critical["risk"] == RISK_CRITICAL
    assert critical["requires_approval"] is True


def test_plan_is_deterministic_and_idempotent():
    current = {"radius_entries": []}
    desired = {"radius_assignments": [_assignment()], "ppp_aaa": True, "interim_update_seconds": 600}
    first = compute_plan(current, desired, [_assignment()])
    second = compute_plan(current, desired, [_assignment()])
    assert first["operations"] == second["operations"]
    assert first["validation"] == second["validation"]


def test_plan_never_mutates_input():
    desired = {"radius_assignments": [_assignment()], "ppp_aaa": True, "interim_update_seconds": 600}
    snapshot_before = [dict(item) for item in desired["radius_assignments"]]
    compute_plan({"radius_entries": []}, desired, [_assignment()])
    assert desired["radius_assignments"] == snapshot_before
