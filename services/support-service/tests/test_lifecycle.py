"""Ticket lifecycle: creation, validated transitions, invalid transitions,
resolution/closure/reopen/duplicate/cancel and the immutable event stream."""
import pytest

from app.domain.exceptions import StateTransitionError, ValidationError
from app.services import ticket_service
from app.services.audit_service import ticket_events
from app.models import TicketResolution


def test_create_ticket_defaults(session, tenant_id, make_ticket):
    ticket = make_ticket()
    assert ticket.status == "NEW"
    assert ticket.customer_status == "SUBMITTED"
    assert ticket.priority == "P2_HIGH"  # HIGH impact x HIGH urgency
    assert ticket.severity == "SEV2"
    assert ticket.ticket_type == "CONNECTIVITY_ISSUE"
    assert ticket.response_deadline is not None
    assert ticket.resolution_deadline is not None
    events = ticket_events(session, ticket.id)
    assert events[0].event_type == "ticket.created"
    assert len(events) == 1
    assert ticket.sla_status == "ACTIVE"


def test_create_ticket_requires_type(session, tenant_id, defaults):
    with pytest.raises(ValidationError):
        ticket_service.create_ticket(session, tenant_id, ticket_type="BOGUS", subject="x",
                                     description="y", customer_id="c")


def test_idempotent_create(session, tenant_id, defaults):
    payload = dict(ticket_type="INQUIRY", subject="dup", description="desc", customer_id="c",
                   idempotency_key="same-key")
    a = ticket_service.create_ticket(session, tenant_id, **payload)
    session.commit()
    b = ticket_service.create_ticket(session, tenant_id, **payload)
    session.commit()
    assert a.id == b.id


def test_assign_transition(session, tenant_id, make_ticket):
    ticket = make_ticket()
    ticket_service.assign(session, tenant_id, ticket.id, agent_id="agent-1", agent_name="Agent One")
    session.commit()
    assert ticket.status == "ASSIGNED"
    assert ticket.assigned_agent_id == "agent-1"
    assert ticket.customer_status == "SUBMITTED"


def test_accept_start_work(session, tenant_id, make_ticket):
    ticket = make_ticket()
    ticket_service.accept(session, tenant_id, ticket.id)
    session.commit()
    assert ticket.status == "IN_PROGRESS"
    assert ticket.customer_status == "IN_PROGRESS"


def test_resolve_requires_code_and_summary(session, tenant_id, make_ticket):
    ticket = make_ticket()
    with pytest.raises(ValidationError):
        ticket_service.resolve(session, tenant_id, ticket.id, resolution_code="X", summary="  ")
    ticket_service.resolve(session, tenant_id, ticket.id, resolution_code="NO_FAULT_FOUND",
                           summary="Verified service healthy.")
    session.commit()
    assert ticket.status == "RESOLVED"
    assert ticket.resolution_code == "NO_FAULT_FOUND"
    resolution = session.query(TicketResolution).filter_by(ticket_id=ticket.id).first()
    assert resolution.summary == "Verified service healthy."


def test_close_requires_resolved(session, tenant_id, make_ticket):
    ticket = make_ticket()
    with pytest.raises(StateTransitionError):
        ticket_service.close(session, tenant_id, ticket.id)
    ticket_service.resolve(session, tenant_id, ticket.id, resolution_code="WORKAROUND_PROVIDED", summary="ok")
    session.commit()
    ticket_service.close(session, tenant_id, ticket.id)
    session.commit()
    assert ticket.status == "CLOSED"
    assert ticket.csat_eligible is True


def test_invalid_transition_rejected(session, tenant_id, make_ticket):
    ticket = make_ticket()
    with pytest.raises(StateTransitionError):
        ticket_service.close(session, tenant_id, ticket.id)  # NEW -> CLOSED is invalid


def test_reopen_from_closed(session, tenant_id, make_ticket):
    ticket = make_ticket()
    ticket_service.resolve(session, tenant_id, ticket.id, resolution_code="CUSTOMER_EDUCATION", summary="explained")
    session.commit()
    ticket_service.close(session, tenant_id, ticket.id)
    session.commit()
    ticket_service.reopen(session, tenant_id, ticket.id, reason="customer says still broken")
    session.commit()
    assert ticket.status == "REOPENED"
    assert ticket.reopened_count == 1
    assert ticket.csat_eligible is False


def test_cancel_requires_reason(session, tenant_id, make_ticket):
    ticket = make_ticket()
    with pytest.raises(ValidationError):
        ticket_service.cancel(session, tenant_id, ticket.id, reason="  ")
    ticket_service.cancel(session, tenant_id, ticket.id, reason="customer changed mind")
    session.commit()
    assert ticket.status == "CANCELLED"
    with pytest.raises(StateTransitionError):
        ticket_service.cancel(session, tenant_id, ticket.id, reason="again")


def test_mark_duplicate(session, tenant_id, make_ticket):
    original = make_ticket()
    duplicate = make_ticket()
    ticket_service.mark_duplicate(session, tenant_id, duplicate.id, original_ticket_id=original.id, reason="same issue")
    session.commit()
    assert duplicate.status == "DUPLICATE"
    assert duplicate.customer_status == "CLOSED"


def test_every_command_appends_immutable_event(session, tenant_id, make_ticket):
    ticket = make_ticket()
    ticket_service.assign(session, tenant_id, ticket.id, agent_id="a1")
    ticket_service.accept(session, tenant_id, ticket.id)
    ticket_service.change_priority(session, tenant_id, ticket.id, priority="P3_MEDIUM", reason="re-eval")
    ticket_service.resolve(session, tenant_id, ticket.id, resolution_code="CONFIGURATION_CORRECTED", summary="fixed")
    ticket_service.close(session, tenant_id, ticket.id)
    session.commit()
    events = ticket_events(session, ticket.id)
    types = [e.event_type for e in events]
    assert types[0] == "ticket.created"
    assert "ticket.assigned" in types
    assert "ticket.accepted" in types
    assert "ticket.priority_changed" in types
    assert "ticket.resolved" in types
    assert "ticket.closed" in types
    versions = [e.aggregate_version for e in events]
    assert versions == sorted(versions)
    assert len(set(versions)) == len(versions)


def test_events_are_append_only(session, tenant_id, make_ticket):
    ticket = make_ticket()
    before = ticket_events(session, ticket.id)
    ticket_service.assign(session, tenant_id, ticket.id, agent_id="a1")
    session.commit()
    after = ticket_events(session, ticket.id)
    assert len(after) == len(before) + 1
    # The original event content is untouched.
    assert after[0].payload["ticket_number"] == ticket.ticket_number


def test_watchers_add_remove(session, tenant_id, make_ticket):
    ticket = make_ticket()
    ticket_service.add_watcher(session, tenant_id, ticket.id, watcher_type="AGENT", watcher_id="w1")
    session.commit()
    ticket_service.remove_watcher(session, tenant_id, ticket.id, watcher_type="AGENT", watcher_id="w1")
    session.commit()
    events = [e.event_type for e in ticket_events(session, ticket.id)]
    assert "ticket.watcher_added" in events
    assert "ticket.watcher_removed" in events


def test_link_related(session, tenant_id, make_ticket):
    a = make_ticket()
    b = make_ticket()
    ticket_service.link_related(session, tenant_id, a.id, relation_type="LINKED", to_ticket_id=b.id)
    session.commit()
    events = [e.event_type for e in ticket_events(session, a.id)]
    assert "ticket.relationship_linked" in events
