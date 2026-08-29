"""End-to-end scenarios covering the complete support workflow.

Scenarios: outage ticket lifecycle with CSAT; payment-not-reflected; speed+FUP;
PPPoE auth failure; field visit; failed OSS order; SLA breach escalation via the
worker; customer reopen; and duplicate delivery deduplication."""
from datetime import datetime, timedelta, timezone

import pytest

from app.domain.exceptions import DuplicateError
from app.integrations.fakes import STATE
from app.messaging.consumers import handle_event
from app.models import TicketEscalation, TicketSLA
from app.services import (
    action_service,
    communication_service,
    diagnostic_service,
    outage_service,
    sla_service,
    ticket_service,
)
from app.services.audit_service import ticket_events


def _now():
    return datetime.now(timezone.utc)


def _make_sla_policy(session, tenant_id):
    from app.services.catalog_service import get_or_create_calendar

    calendar = get_or_create_calendar(session, tenant_id)
    calendar.working_hours = {d: [["00:00", "24:00"]] for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}
    session.flush()
    policy = sla_service.create_policy(session, tenant_id, code="E2E_SLA", name="E2E SLA")
    sla_service.create_version(
        session, tenant_id, policy.id,
        definition={
            "pause_on_states": ["PENDING_CUSTOMER"],
            "reopen_policy": "RESTART",
            "reset_on_reassign": False,
            "escalation": [
                {"target": "RESPONSE", "at_risk_pct": 50, "level": 1, "action": "NOTIFY_AGENT"},
                {"target": "RESOLUTION", "at_risk_pct": 50, "level": 1, "action": "NOTIFY_TEAM_LEAD"},
            ],
        },
        targets=[
            {"priority": "ALL", "kind": "RESPONSE", "business_seconds": 3600},
            {"priority": "ALL", "kind": "RESOLUTION", "business_seconds": 7200},
        ],
        activate=True)
    session.commit()


def test_e2e_outage_ticket_to_csat(session, tenant_id, defaults, make_ticket):
    # 1. Customer creates a no-internet ticket.
    STATE.nms["known_outage"] = "OUT-0001"
    STATE.nms["nas_health"] = "DOWN"
    ticket = make_ticket(ticket_type="OUTAGE_RELATED")
    session.refresh(ticket)

    # 2. Diagnostics identify the known outage.
    snapshot = diagnostic_service.capture_diagnostic_snapshot(session, tenant_id, ticket, actor="agent")
    checks = {c["name"]: c["status"] for c in snapshot.snapshot["checks"]}
    assert checks["known_outage"] == "FAIL"
    assert checks["nas_unreachable"] == "FAIL"

    # 3. Ticket links to the NMS incident.
    incident = {"id": "OUT-0001", "number": "OUT-0001", "service_location": "loc-1"}
    outage_service.link_incident(session, tenant_id, ticket.id, incident_id=incident["id"], incident_number=incident["number"])
    session.commit()
    assert ticket.nms_incident_id == "OUT-0001"

    # 4. Agent communicates a status update.
    communication_service.add_comment(session, tenant_id, ticket, kind="PUBLIC_REPLY", body="We are aware of the outage",
                                      sender_type="AGENT", sender_id="a1")
    session.commit()

    # 5. Outage clears; verification is pending but no auto-close.
    outage_service.handle_outage_cleared(session, tenant_id, "OUT-0001", actor="nms-consumer")
    session.commit()
    assert ticket.status != "CLOSED"

    # 6. Agent verifies and resolves.
    ticket_service.resolve(session, tenant_id, ticket.id, resolution_code="KNOWN_OUTAGE_RESOLVED",
                           summary="service restored and verified")
    session.commit()
    assert ticket.status == "RESOLVED"

    # 7. Close and collect CSAT.
    ticket_service.close(session, tenant_id, ticket.id)
    session.commit()
    csat = ticket_service.submit_csat(session, tenant_id, ticket.id, rating=4)
    session.commit()
    assert csat["rating"] == 4

    events = [e.event_type for e in ticket_events(session, ticket.id)]
    for expected in ("ticket.created", "ticket.outage_linked", "ticket.public_reply_sent",
                     "ticket.outage_cleared_verification_pending", "ticket.resolved", "ticket.closed", "ticket.csat_received"):
        assert expected in events


def test_e2e_payment_not_reflected(session, tenant_id, defaults, make_ticket):
    ticket = make_ticket(ticket_type="PAYMENT_QUERY", billing_account_id="BA-1")
    action = action_service.request_action(session, tenant_id, ticket.id, action_type="REQUEST_PAYMENT_RECONCILIATION", actor="a1")
    session.commit()
    action_service.execute_action(session, tenant_id, action.id)
    session.commit()
    assert action.status == "SUCCEEDED"
    ticket_service.resolve(session, tenant_id, ticket.id, resolution_code="PAYMENT_RECONCILED", summary="payment reflected")
    session.commit()
    assert ticket.status == "RESOLVED"


def test_e2e_speed_issue_with_fup(session, tenant_id, defaults, make_ticket):
    STATE.policy["applied_bandwidth"] = 20000
    STATE.policy["expected_bandwidth"] = 100000
    STATE.policy["fup_state"] = "THROTTLED"
    ticket = make_ticket(ticket_type="SPEED_ISSUE")
    snapshot = diagnostic_service.capture_diagnostic_snapshot(session, tenant_id, ticket, actor="agent")
    checks = {c["name"]: c["status"] for c in snapshot.snapshot["checks"]}
    assert checks["speed_mismatch"] == "FAIL"
    assert checks["fup_throttling"] == "WARN"
    # Agent requests policy reapplication (disruptive -> approved by supervisor).
    action = action_service.request_action(session, tenant_id, ticket.id, action_type="REAPPLY_SESSION_POLICY", actor="a1")
    session.commit()
    action_service.approve_action(session, tenant_id, action.id, actor="sup", reason="customer consented to session reset")
    session.commit()
    action_service.execute_action(session, tenant_id, action.id)
    session.commit()
    assert action.status == "SUCCEEDED"


def test_e2e_pppoe_auth_failure(session, tenant_id, defaults, make_ticket):
    STATE.sessions["auth_failures"] = [{"username": "subs-0001", "result": "REJECT"}]
    STATE.sessions["last_auth_result"] = "REJECT"
    STATE.sessions["active_sessions"] = []
    ticket = make_ticket(ticket_type="AUTHENTICATION_ISSUE", category_code="AUTHENTICATION")
    snapshot = diagnostic_service.capture_diagnostic_snapshot(session, tenant_id, ticket, actor="agent")
    checks = {c["name"]: c["status"] for c in snapshot.snapshot["checks"]}
    assert checks["recent_auth_rejects"] == "FAIL"
    # Controlled disconnect-reauth (approved).
    action = action_service.request_action(session, tenant_id, ticket.id, action_type="DISCONNECT_REAUTHORIZE", actor="a1")
    session.commit()
    action_service.approve_action(session, tenant_id, action.id, actor="sup", reason="auth retry approved")
    session.commit()
    action_service.execute_action(session, tenant_id, action.id)
    session.commit()
    assert action.status == "SUCCEEDED"


def test_e2e_field_visit_workflow(session, tenant_id, defaults, make_ticket):
    ticket = make_ticket()
    action = action_service.request_action(session, tenant_id, ticket.id, action_type="CREATE_WORKFORCE_JOB",
                                           payload={"job_type": "FIELD_VISIT", "notes": "ONT power light off"}, actor="a1")
    session.commit()
    action_service.execute_action(session, tenant_id, action.id)
    session.commit()
    assert ticket.status == "PENDING_FIELD_VISIT"
    assert ticket.workforce_job_id
    # Technician completes the job; workforce publishes; ticket returns to work.
    handle_event(session, {"id": "evt-e2e-job", "event_type": "workforce.job_completed.v1",
                           "tenant_id": str(tenant_id), "payload": {"job_id": ticket.workforce_job_id}})
    session.refresh(ticket)
    assert ticket.status == "IN_PROGRESS"
    ticket_service.resolve(session, tenant_id, ticket.id, resolution_code="DEVICE_REPLACED", summary="ONT replaced")
    session.commit()
    assert ticket.status == "RESOLVED"


def test_e2e_failed_oss_order_escalates(session, tenant_id, defaults, make_ticket):
    ticket = make_ticket(ticket_type="SERVICE_REQUEST", category_code="PROVISIONING")
    ticket_service.link_oss_order(session, tenant_id, ticket.id, order_id="ORD-FAIL-1", order_number="ORD-FAIL-1", actor="a1")
    session.commit()
    assert ticket.status == "PENDING_OSS_ORDER"
    # OSS reports failure; the ticket is marked and escalated.
    handle_event(session, {"id": "evt-order-fail", "event_type": "oss.order.failed.v1",
                           "tenant_id": str(tenant_id), "payload": {"order_id": "ORD-FAIL-1", "order_number": "ORD-FAIL-1"}})
    session.refresh(ticket)
    escalations = session.query(TicketEscalation).filter_by(ticket_id=ticket.id).all()
    assert any(e.trigger == "FAILED_OSS_ORDER" for e in escalations)


def test_e2e_sla_breach_via_worker(session, tenant_id, defaults, make_ticket):
    _make_sla_policy(session, tenant_id)
    ticket = make_ticket(priority="P3_MEDIUM")
    sla = session.query(TicketSLA).filter_by(ticket_id=ticket.id).first()
    sla.resolution_deadline = _now() - timedelta(seconds=10)
    sla.status = "ACTIVE"
    session.commit()
    from app import tasks

    result = tasks.evaluate_sla_deadlines(session)
    assert result["breached"] == 1
    session.refresh(ticket)
    escalations = session.query(TicketEscalation).filter_by(ticket_id=ticket.id).all()
    assert any(e.trigger == "SLA_BREACH" for e in escalations)


def test_e2e_duplicate_delivery_deduplicated(session, tenant_id, defaults, make_ticket):
    ticket = make_ticket()
    message_id = "whatsapp-123"
    communication_service.add_comment(session, tenant_id, ticket, kind="CUSTOMER_MESSAGE", direction="INBOUND",
                                      body="my net is down", channel="WHATSAPP", sender_type="CUSTOMER",
                                      sender_id=ticket.customer_id, provider_message_id=message_id)
    session.commit()
    from app.domain.exceptions import DuplicateError

    import pytest

    with pytest.raises(DuplicateError):
        communication_service.add_comment(session, tenant_id, ticket, kind="CUSTOMER_MESSAGE", direction="INBOUND",
                                          body="my net is down (duplicate)", channel="WHATSAPP", sender_type="CUSTOMER",
                                          sender_id=ticket.customer_id, provider_message_id=message_id)
    # Only one customer-replied event.
    events = [e.event_type for e in ticket_events(session, ticket.id)]
    assert events.count("ticket.customer_replied") == 1
