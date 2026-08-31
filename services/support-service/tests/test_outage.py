"""Outage correlation tests: suggestions, linking, auto-association, cleared
handling (no auto-close) and idempotent event consumption."""
from datetime import datetime, timedelta, timezone

from app.integrations.fakes import STATE
from app.messaging.consumers import handle_event
from app.services import outage_service


def _outage(**kw):
    base = {
        "id": "INC-1",
        "number": "INC-1",
        "pop": "pop-1",
        "nas": "nas-1",
        "service_location": "loc-1",
        "started_at": (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat(),
    }
    base.update(kw)
    return base


def test_suggest_incidents_matches_location(session, tenant_id, defaults, make_ticket):
    STATE.outages.append(_outage())
    ticket = make_ticket()
    suggestions = outage_service.suggest_incidents(session, tenant_id, ticket)
    assert len(suggestions) == 1
    assert suggestions[0]["id"] == "INC-1"


def test_suggest_ignores_old_outages(session, tenant_id, defaults, make_ticket):
    STATE.outages.append(_outage(started_at=(datetime.now(timezone.utc) - timedelta(days=3)).isoformat()))
    ticket = make_ticket()
    assert outage_service.suggest_incidents(session, tenant_id, ticket) == []


def test_link_incident(session, tenant_id, make_ticket):
    ticket = make_ticket()
    outage_service.link_incident(session, tenant_id, ticket.id, incident_id="INC-1", incident_number="INC-1")
    session.commit()
    assert ticket.nms_incident_id == "INC-1"
    from app.services.audit_service import ticket_events

    events = [e.event_type for e in ticket_events(session, ticket.id)]
    assert "ticket.outage_linked" in events


def test_auto_associate_tickets(session, tenant_id, defaults, make_ticket):
    ticket = make_ticket()  # location loc-1
    other = make_ticket(subject="unrelated", service_location_id="loc-9")
    incident = _outage()
    linked = outage_service.auto_associate_tickets(session, tenant_id, incident, actor="nms-consumer")
    session.commit()
    assert ticket.ticket_number in linked
    assert other.ticket_number not in linked
    assert ticket.nms_incident_id == "INC-1"


def test_outage_cleared_requires_verification_not_close(session, tenant_id, defaults, make_ticket):
    ticket = make_ticket()
    outage_service.auto_associate_tickets(session, tenant_id, _outage(), actor="nms-consumer")
    session.commit()
    result = outage_service.handle_outage_cleared(session, tenant_id, "INC-1", actor="nms-consumer")
    session.commit()
    assert ticket.ticket_number in result["verification_needed"]
    assert ticket.status != "CLOSED"  # never auto-closed
    from app.services.audit_service import ticket_events

    events = [e.event_type for e in ticket_events(session, ticket.id)]
    assert "ticket.outage_cleared_verification_pending" in events


def test_consumer_is_idempotent(session, tenant_id, defaults, make_ticket):
    ticket = make_ticket()
    event = {
        "id": "evt-outage-1",
        "event_type": "nms.outage_detected.v1",
        "tenant_id": str(tenant_id),
        "payload": _outage(),
    }
    first = handle_event(session, event)
    second = handle_event(session, event)
    assert first["handled"] is True
    assert second["handled"] is False
    assert second["action"] == "duplicate"


def test_consumer_order_completed_advances_ticket(session, tenant_id, defaults, make_ticket):
    ticket = make_ticket()
    from app.services import ticket_service

    ticket_service.link_oss_order(session, tenant_id, ticket.id, order_id="ORD-1", order_number="ORD-1", actor="agent")
    session.commit()
    assert ticket.status == "PENDING_OSS_ORDER"
    event = {"id": "evt-order-1", "event_type": "oss.order.completed.v1", "tenant_id": str(tenant_id),
             "payload": {"order_id": "ORD-1", "order_number": "ORD-1"}}
    result = handle_event(session, event)
    assert result["handled"] is True
    session.refresh(ticket)
    assert ticket.status == "IN_PROGRESS"


def test_consumer_workforce_job_completed(session, tenant_id, defaults, make_ticket):
    ticket = make_ticket()
    from app.services import ticket_service

    ticket_service.link_workforce_job(session, tenant_id, ticket.id, job_id="JOB-1", job_number="JOB-1", actor="agent")
    session.commit()
    assert ticket.status == "PENDING_FIELD_VISIT"
    event = {"id": "evt-job-1", "event_type": "workforce.job_completed.v1", "tenant_id": str(tenant_id),
             "payload": {"job_id": "JOB-1", "job_number": "JOB-1"}}
    handle_event(session, event)
    session.refresh(ticket)
    assert ticket.status == "IN_PROGRESS"
