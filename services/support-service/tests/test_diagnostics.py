"""Diagnostic snapshot tests: full availability, partial source failure,
stale/unavailable data and deterministic checks."""
from app.integrations.fakes import STATE
from app.services import diagnostic_service, ticket_service


def _snapshot(session, tenant_id, ticket):
    return diagnostic_service.capture_diagnostic_snapshot(session, tenant_id, ticket, actor="test")


def test_snapshot_complete_with_defaults(session, tenant_id, make_ticket):
    ticket = make_ticket()
    snapshot = _snapshot(session, tenant_id, ticket)
    assert snapshot.status == "COMPLETE"
    sources = snapshot.snapshot["sources"]
    assert sources["crm"]["status"] == "COMPLETE"
    assert sources["bss"]["status"] == "COMPLETE"
    assert sources["oss"]["status"] == "COMPLETE"
    assert sources["aaa"]["status"] == "COMPLETE"
    assert sources["network"]["status"] == "COMPLETE"
    assert sources["nms"]["status"] == "COMPLETE"


def test_partial_source_failure_reported(session, tenant_id, make_ticket):
    STATE.fail["crm"] = "timeout"
    STATE.fail["bss"] = "down"
    ticket = make_ticket()
    snapshot = _snapshot(session, tenant_id, ticket)
    assert snapshot.status == "PARTIAL"
    assert snapshot.snapshot["sources"]["crm"]["status"] == "FAILED"
    assert snapshot.snapshot["sources"]["bss"]["status"] == "FAILED"
    # Failing sources must not be pretended healthy: their checks become UNKNOWN.
    checks = {c["name"]: c for c in snapshot.snapshot["checks"]}
    assert checks["financial_suspension"]["status"] == "UNKNOWN"


def test_deterministic_checks_detected(session, tenant_id, make_ticket):
    STATE.billing["financial_restriction"] = "DUES"
    STATE.subscriber["suspension_state"] = "ADMIN"
    STATE.sessions["auth_failures"] = [{"username": "subs-0001", "result": "REJECT"}]
    STATE.policy["applied_bandwidth"] = 50000  # < expected 100000
    STATE.policy["fup_state"] = "THROTTLED"
    STATE.policy["policy_drift"] = True
    STATE.nms["nas_health"] = "DOWN"
    STATE.nms["known_outage"] = "OUT-0001"
    STATE.nms["onu_health"] = "OFFLINE"
    STATE.nms["recent_alarms"] = ["LOS"]
    STATE.subscriber["assigned_ip"] = "10.9.9.9"  # mismatch vs active session framed_ip 10.1.1.10

    ticket = make_ticket()
    snapshot = _snapshot(session, tenant_id, ticket)
    checks = {c["name"]: c for c in snapshot.snapshot["checks"]}
    assert checks["financial_suspension"]["status"] == "WARN"
    assert checks["service_suspension"]["status"] == "WARN"
    assert checks["recent_auth_rejects"]["status"] == "FAIL"
    assert checks["speed_mismatch"]["status"] == "FAIL"
    assert checks["fup_throttling"]["status"] == "WARN"
    assert checks["config_drift"]["status"] == "WARN"
    assert checks["nas_unreachable"]["status"] == "FAIL"
    assert checks["known_outage"]["status"] == "FAIL"
    assert checks["ont_offline"]["status"] == "WARN"
    assert checks["los_alarm"]["status"] == "WARN"
    assert checks["ip_mismatch"]["status"] == "WARN"
    # Every check is explainable with a suggested action + permission.
    for check in snapshot.snapshot["checks"]:
        assert check["suggested_action"]
        assert check["required_permission"]
        assert check["timestamp"]
        assert check["severity"]


def test_duplicate_sessions_detected(session, tenant_id, make_ticket):
    STATE.sessions["active_sessions"] = [
        {"session_id": "s1", "framed_ip": "10.1.1.10"},
        {"session_id": "s2", "framed_ip": "10.1.1.11"},
    ]
    ticket = make_ticket()
    snapshot = _snapshot(session, tenant_id, ticket)
    checks = {c["name"]: c for c in snapshot.snapshot["checks"]}
    assert checks["duplicate_sessions"]["status"] == "WARN"


def test_no_active_session_detected(session, tenant_id, make_ticket):
    STATE.sessions["active_sessions"] = []
    ticket = make_ticket()
    snapshot = _snapshot(session, tenant_id, ticket)
    checks = {c["name"]: c for c in snapshot.snapshot["checks"]}
    assert checks["no_active_session"]["status"] == "WARN"


def test_refresh_diagnostics_command(session, tenant_id, make_ticket):
    ticket = make_ticket()
    before = diagnostic_service.latest_snapshot(session, ticket.id)
    STATE.nms["nas_health"] = "DOWN"
    diagnostic_service.capture_diagnostic_snapshot(session, tenant_id, ticket, actor="agent")
    session.commit()
    after = diagnostic_service.latest_snapshot(session, ticket.id)
    assert after.id != before.id
    assert after.snapshot["sources"]["nms"]["status"] == "COMPLETE"
    assert any(c["name"] == "nas_unreachable" for c in after.snapshot["checks"])


def test_snapshot_captured_as_event(session, tenant_id, make_ticket):
    ticket = make_ticket()
    diagnostic_service.capture_diagnostic_snapshot(session, tenant_id, ticket, actor="agent")
    session.commit()
    from app.services.audit_service import ticket_events

    events = [e.event_type for e in ticket_events(session, ticket.id)]
    assert "ticket.diagnostic_snapshot_captured" in events
