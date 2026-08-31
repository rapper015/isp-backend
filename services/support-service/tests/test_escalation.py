"""Escalation tests: SLA-driven, no-assignment, repeated-reopen triggers and
explainable/auditable escalation actions."""
from datetime import datetime, timedelta, timezone

from app.domain.sla import engine as sla_engine
from app.models import TicketEscalation
from app.services import escalation_service, sla_service, ticket_service
from app.services.audit_service import ticket_events


def test_sla_breach_triggers_escalation(session, tenant_id, defaults, make_ticket):
    ticket = make_ticket(priority="P3_MEDIUM")
    sla = sla_service.get_ticket_sla(session, ticket)
    # Force breach state, then detect triggers.
    result = sla_engine.evaluate_sla(session, sla, now=sla.resolution_deadline + timedelta(minutes=1))
    assert result["breached"] is True
    session.flush()
    triggers = escalation_service.detect_triggers(session, tenant_id, ticket)
    assert any(t["trigger"] == "SLA_BREACH" for t in triggers)
    fired = escalation_service.evaluate_ticket(session, tenant_id, ticket, actor="worker")
    session.commit()
    assert "SLA_BREACH" in fired
    escalations = session.query(TicketEscalation).filter_by(ticket_id=ticket.id).all()
    assert len(escalations) == 1
    assert escalations[0].trigger == "SLA_BREACH"
    assert escalations[0].actions
    assert escalations[0].status == "OPEN"
    events = [e.event_type for e in ticket_events(session, ticket.id)]
    assert "ticket.escalated" in events


def test_no_assignment_escalation(session, tenant_id, defaults, make_ticket):
    ticket = make_ticket()
    # Age the ticket beyond the no-assignment threshold.
    ticket.updated_at = datetime.now(timezone.utc) - timedelta(hours=6)
    session.commit()
    fired = escalation_service.evaluate_ticket(session, tenant_id, ticket, actor="worker")
    session.commit()
    assert "NO_ASSIGNMENT" in fired
    escalations = session.query(TicketEscalation).filter_by(ticket_id=ticket.id).all()
    assert escalations[0].trigger == "NO_ASSIGNMENT"


def test_repeated_reopen_escalation(session, tenant_id, defaults, make_ticket):
    ticket = make_ticket()
    ticket.reopened_count = 4
    session.commit()
    fired = escalation_service.evaluate_ticket(session, tenant_id, ticket, actor="worker")
    session.commit()
    assert "REPEATED_REOPEN" in fired
    escalations = session.query(TicketEscalation).filter_by(ticket_id=ticket.id).all()
    assert escalations[0].trigger == "REPEATED_REOPEN"
    assert "REQUIRE_MANAGEMENT_REVIEW" in escalations[0].actions


def test_escalation_deduplicated_per_trigger(session, tenant_id, defaults, make_ticket):
    ticket = make_ticket()
    ticket.reopened_count = 4
    session.commit()
    escalation_service.evaluate_ticket(session, tenant_id, ticket, actor="worker")
    session.commit()
    # Second evaluation must not create a duplicate escalation for the same trigger.
    fired = escalation_service.evaluate_ticket(session, tenant_id, ticket, actor="worker")
    session.commit()
    assert "REPEATED_REOPEN" not in fired
    count = session.query(TicketEscalation).filter_by(ticket_id=ticket.id, trigger="REPEATED_REOPEN").count()
    assert count == 1


def test_manual_escalate_command(session, tenant_id, make_ticket):
    ticket = make_ticket()
    ticket_service.escalate(session, tenant_id, ticket.id, reason="customer escalation", trigger="CUSTOMER_ESCALATION")
    session.commit()
    assert ticket.status == "ESCALATED"
    assert ticket.escalation_level == 1
    escalations = session.query(TicketEscalation).filter_by(ticket_id=ticket.id).all()
    assert any(e.trigger == "CUSTOMER_ESCALATION" for e in escalations)


def test_resolve_escalation(session, tenant_id, defaults, make_ticket):
    ticket = make_ticket()
    ticket.reopened_count = 4
    session.commit()
    escalation_service.evaluate_ticket(session, tenant_id, ticket, actor="worker")
    session.commit()
    escalation_service.resolve_escalation(session, ticket, trigger="REPEATED_REOPEN")
    session.commit()
    escalations = session.query(TicketEscalation).filter_by(ticket_id=ticket.id).all()
    assert escalations[0].status == "RESOLVED"
    assert escalations[0].resolved_at is not None
