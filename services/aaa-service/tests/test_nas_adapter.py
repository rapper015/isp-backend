"""Adapter tests against the in-memory fake router and pure capability logic."""
from types import SimpleNamespace

import pytest

from app.nas_service import build_adapter
from app.routeros import (FakeRouterOSAdapter, RouterOSApiAdapter, RouterOSAuthenticationError, RouterOSCommandError, RouterOSPermissionError, adapter_for_version, detect_capability_flags, normalize_radius_entry, parse_routeros_version, redact)


def test_connection_test_returns_normalized_result():
    adapter = FakeRouterOSAdapter(identity="pop-1", version="7.15")
    result = adapter.test_connection()
    assert result["ok"] is True
    assert result["identity"] == "pop-1"
    assert result["version"] == "7.15"
    assert "latency_ms" in result


def test_authentication_failure_is_structured():
    adapter = FakeRouterOSAdapter()
    adapter.fail_auth = True
    with pytest.raises(RouterOSAuthenticationError) as error:
        adapter.connect()
    assert error.value.code == "AUTHENTICATION_FAILED"


def test_permission_failure_is_structured():
    adapter = FakeRouterOSAdapter()
    adapter.fail_permission = True
    with pytest.raises(RouterOSPermissionError) as error:
        adapter.connect()
    assert error.value.code == "INSUFFICIENT_PERMISSION"


def test_command_failure_is_structured():
    adapter = FakeRouterOSAdapter()
    adapter.command_error = "no such command"
    with pytest.raises(RouterOSCommandError) as error:
        adapter.detect_capabilities()
    assert error.value.code == "COMMAND_FAILED"


def test_version_discovery():
    adapter = FakeRouterOSAdapter(version="7.15")
    assert adapter.get_version() == "7.15"
    assert adapter.get_system_resource()["board_name"] == "CCR2004"
    assert parse_routeros_version(adapter.get_version()) == (7, 15, 0)


def test_create_update_remove_managed_entry():
    adapter = FakeRouterOSAdapter()
    remote_id = adapter.create_radius_entry({"address": "10.0.0.10", "secret": "topsecret", "service": ["pppoe"], "accounting_port": 1813, "timeout": 3000})
    assert adapter.get_radius_entries()[0]["address"] == "10.0.0.10"
    # Secret must never leak through reads.
    assert "topsecret" not in str(redact(adapter.get_relevant_service_state()))
    adapter.update_radius_entry(remote_id, {"service": ["pppoe", "hotspot"], "timeout": 5000})
    updated = adapter.get_radius_entries()[0]
    assert updated["service"] == ["pppoe", "hotspot"]
    assert updated["timeout"] == 5000
    adapter.remove_radius_entry(remote_id)
    assert adapter.get_radius_entries() == []


def test_ppp_aaa_configuration():
    adapter = FakeRouterOSAdapter()
    adapter.configure_ppp_aaa({"use_radius": True, "accounting": True, "interim_update_seconds": 600})
    assert adapter.get_ppp_aaa()["use_radius"] is True
    assert adapter.get_ppp_aaa()["interim_update"] == "10m"


def test_user_aaa_configuration():
    adapter = FakeRouterOSAdapter()
    adapter.configure_user_aaa({"use_radius": True, "default_group": "full", "excluded_groups": ["read"]})
    state = adapter.get_user_aaa()
    assert state["use_radius"] is True
    assert state["default_group"] == "full"
    assert state["excluded_groups"] == ["read"]


def test_hotspot_radius_configuration_preserves_unrelated_profiles():
    adapter = FakeRouterOSAdapter()
    adapter.seed_hotspot_profile(name="default", use_radius=False, radius_accounting=False)
    adapter.seed_hotspot_profile(name="guest", use_radius=False, radius_accounting=False)
    adapter.configure_hotspot_radius("default", {"use_radius": True, "radius_accounting": True, "radius_interim_update_seconds": 600})
    profiles = {item["name"]: item for item in adapter.get_hotspot_profiles()}
    assert profiles["default"]["use_radius"] is True
    assert profiles["guest"]["use_radius"] is False  # untouched


def test_radius_incoming_configuration():
    adapter = FakeRouterOSAdapter()
    adapter.configure_radius_incoming({"accept": True, "port": 3799})
    incoming = adapter.get_radius_incoming()[0]
    assert incoming["accept"] is True
    assert incoming["port"] == 3799


def test_capability_mapping_v6_vs_v7():
    v6 = detect_capability_flags((6, 45, 0))
    v7 = detect_capability_flags((7, 15, 0))
    assert v6["message_authenticator_options"] is True
    assert v7["message_authenticator_options"] is True
    assert detect_capability_flags((6, 30, 0))["message_authenticator_options"] is False
    assert detect_capability_flags(None)["routeros_api"] is False


def test_adapter_for_version():
    v6 = adapter_for_version("6.49.10", host="10.0.0.1", username="u", password="p")
    v7 = adapter_for_version("7.15", host="10.0.0.1", username="u", password="p")
    assert v6.version == "6"
    assert v7.version == "7"
    with pytest.raises(Exception):
        adapter_for_version("5.0", host="x", username="u", password="p")


def test_malformed_version_is_unsupported():
    adapter = FakeRouterOSAdapter(version="not-a-version")
    # A malformed version cannot be parsed; capability flags default to False.
    assert parse_routeros_version(adapter.get_version()) is None
    assert detect_capability_flags(None)["routeros_api"] is False


def test_active_sessions_are_normalized():
    adapter = FakeRouterOSAdapter()
    adapter.active_ppp.append({"name": "user1", "address": "10.0.0.5", "service": "pppoe", "uptime": "1m"})
    assert adapter.get_active_ppp_sessions()[0]["name"] == "user1"
    assert adapter.get_active_hotspot_sessions() == []


def test_routeros_7_auto_mode_uses_modern_plaintext_login(monkeypatch):
    monkeypatch.setattr("app.nas_service.decrypt_secret", lambda value: value)
    monkeypatch.delenv("AAA_ROUTEROS_ADAPTER", raising=False)
    nas = SimpleNamespace(
        management_host="10.0.0.1", source_ip="10.0.0.1", management_port=8728,
        management_protocol="api", tls_verify=False, routeros_version=None,
        api_mode="auto",
    )
    credential = SimpleNamespace(
        username_ciphertext="api-user", secret_ciphertext="api-password",
        api_port=8728, tls_settings={},
    )

    adapter = build_adapter(nas, credential)

    assert isinstance(adapter, RouterOSApiAdapter)
    assert adapter.plaintext_login is True


def test_radius_entry_translates_pppoe_to_routeros_ppp():
    adapter = RouterOSApiAdapter("router.example", "admin", "password")

    arguments = adapter._radius_entry_arguments(
        {"address": "192.0.2.10", "services": ["pppoe", "login"], "timeout": 3000}
    )

    assert arguments["service"] == "ppp,login"
    assert arguments["timeout"] == "3000ms"


def test_radius_entry_normalizes_routeros_ppp_and_cleaned_id():
    entry = normalize_radius_entry(
        {"id": "*1", "address": "192.0.2.10", "service": "ppp,login"}
    )

    assert entry["remote_id"] == "*1"
    assert entry["service"] == ["pppoe", "login"]


def test_routeros_723_empty_reply_is_an_empty_result(monkeypatch):
    adapter = RouterOSApiAdapter("10.0.0.1", "user", "password", use_ssl=False)
    adapter._api = object()

    class EmptyResource:
        def call(self, *_args, **_kwargs):
            raise Exception("Malformed sentence", [b"!empty", b".tag=1"])

    monkeypatch.setattr(adapter, "_resource", lambda _path: EmptyResource())

    assert adapter._call("/radius", "print") == []
