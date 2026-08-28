"""Unit tests: drift detection classification."""
from app.nas_drift import DRIFT_CRITICAL, DRIFT_NONE, DRIFT_SAFE, DRIFT_UNKNOWN, DRIFT_WARNING, detect_drift


def _desired(**kwargs):
    base = {"radius_assignments": [{"assignment_id": "a1", "address": "10.0.0.10", "services": ["pppoe"], "auth_port": 1812, "accounting_port": 1813, "timeout": 3000, "src_address": None}], "ppp_aaa": True, "incoming_coa": True, "hotspot_profiles": [], "login_radius": False}
    base.update(kwargs)
    return base


def test_no_drift_when_state_matches():
    current = {"radius_entries": [{"remote_id": "*1", "address": "10.0.0.10", "service": ["pppoe"], "authentication_port": 1812, "accounting_port": 1813, "timeout": 3000, "src_address": None}], "ppp_aaa": {"use_radius": True, "accounting": True}, "radius_incoming": {"accept": True}, "hotspot_profiles": []}
    result = detect_drift(current, _desired())
    assert result["classification"] == DRIFT_NONE


def test_missing_radius_entry_is_critical():
    result = detect_drift({"radius_entries": [], "ppp_aaa": {}, "radius_incoming": {}, "hotspot_profiles": []}, _desired())
    assert result["classification"] == DRIFT_CRITICAL
    assert any(item["kind"] == "missing_radius_entry" for item in result["items"])


def test_changed_port_is_warning():
    current = {"radius_entries": [{"remote_id": "*1", "address": "10.0.0.10", "service": ["pppoe"], "authentication_port": 1812, "accounting_port": 1814, "timeout": 3000, "src_address": None}], "ppp_aaa": {"use_radius": True, "accounting": True}, "radius_incoming": {"accept": True}, "hotspot_profiles": []}
    result = detect_drift(current, _desired())
    assert result["classification"] == DRIFT_WARNING
    assert any(item["kind"] == "radius_entry_accounting_port" for item in result["items"])


def test_ppp_aaa_change_is_warning():
    current = {"radius_entries": [{"remote_id": "*1", "address": "10.0.0.10", "service": ["pppoe"], "authentication_port": 1812, "accounting_port": 1813, "timeout": 3000, "src_address": None}], "ppp_aaa": {"use_radius": False, "accounting": False}, "radius_incoming": {"accept": True}, "hotspot_profiles": []}
    result = detect_drift(current, _desired())
    assert any(item["kind"] == "ppp_aaa_changed" for item in result["items"])


def test_incoming_coa_disabled_is_warning():
    current = {"radius_entries": [{"remote_id": "*1", "address": "10.0.0.10", "service": ["pppoe"], "authentication_port": 1812, "accounting_port": 1813, "timeout": 3000, "src_address": None}], "ppp_aaa": {"use_radius": True, "accounting": True}, "radius_incoming": {"accept": False}, "hotspot_profiles": []}
    result = detect_drift(current, _desired())
    assert any(item["kind"] == "incoming_coa_disabled" for item in result["items"])


def test_external_entry_is_safe():
    current = {"radius_entries": [{"remote_id": "*1", "address": "10.0.0.10", "service": ["pppoe"], "authentication_port": 1812, "accounting_port": 1813, "timeout": 3000, "src_address": None}, {"remote_id": "*2", "address": "192.0.2.9", "service": ["hotspot"], "authentication_port": 1812, "accounting_port": 1813, "timeout": 3000, "src_address": None}], "ppp_aaa": {"use_radius": True, "accounting": True}, "radius_incoming": {"accept": True}, "hotspot_profiles": []}
    result = detect_drift(current, _desired())
    assert any(item["kind"] == "unknown_external_entry" for item in result["items"])


def test_unknown_when_no_current_state():
    result = detect_drift(None, _desired())
    assert result["classification"] == DRIFT_UNKNOWN


def test_login_radius_change_is_critical():
    current = {"radius_entries": [{"remote_id": "*1", "address": "10.0.0.10", "service": ["pppoe"], "authentication_port": 1812, "accounting_port": 1813, "timeout": 3000, "src_address": None}], "ppp_aaa": {"use_radius": True, "accounting": True}, "radius_incoming": {"accept": True}, "hotspot_profiles": [], "user_aaa": {"use_radius": False}}
    result = detect_drift(current, _desired(login_radius=True, radius_assignments=[{"assignment_id": "a1", "address": "10.0.0.10", "services": ["pppoe"], "auth_port": 1812, "accounting_port": 1813, "timeout": 3000, "src_address": None}]))
    assert result["classification"] == DRIFT_CRITICAL
    assert any(item["kind"] == "user_aaa_changed" for item in result["items"])
