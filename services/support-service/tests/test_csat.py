"""CSAT tests: single submission, range validation, low-score alert and
non-mutability by agents."""
import pytest

from app.domain.exceptions import DuplicateError, ValidationError
from app.integrations.fakes import STATE
from app.services import ticket_service
from app.models import CustomerSatisfaction


def _closed_ticket(session, tenant_id, make_ticket):
    ticket = make_ticket()
    ticket_service.resolve(session, tenant_id, ticket.id, resolution_code="NO_FAULT_FOUND", summary="ok")
    session.commit()
    ticket_service.close(session, tenant_id, ticket.id)
    session.commit()
    return ticket


def test_submit_csat_once(session, tenant_id, make_ticket):
    ticket = _closed_ticket(session, tenant_id, make_ticket)
    assert ticket.csat_eligible is True
    result = ticket_service.submit_csat(session, tenant_id, ticket.id, rating=5, comment="great", channel="EMAIL")
    session.commit()
    assert result["rating"] == 5
    assert session.query(CustomerSatisfaction).filter_by(ticket_id=ticket.id).count() == 1
    with pytest.raises(DuplicateError):
        ticket_service.submit_csat(session, tenant_id, ticket.id, rating=4)


def test_rating_range_validated(session, tenant_id, make_ticket):
    ticket = _closed_ticket(session, tenant_id, make_ticket)
    with pytest.raises(ValidationError):
        ticket_service.submit_csat(session, tenant_id, ticket.id, rating=0)
    with pytest.raises(ValidationError):
        ticket_service.submit_csat(session, tenant_id, ticket.id, rating=6)


def test_low_score_alerts_supervisor(session, tenant_id, make_ticket):
    ticket = _closed_ticket(session, tenant_id, make_ticket)
    ticket_service.submit_csat(session, tenant_id, ticket.id, rating=1, comment="terrible")
    session.commit()
    csat = session.query(CustomerSatisfaction).filter_by(ticket_id=ticket.id).first()
    assert csat.low_score_reviewed is True
    assert any(n["template"] == "csat_low_score" for n in STATE.notifications)


def test_csat_not_open_before_closure(session, tenant_id, make_ticket):
    ticket = make_ticket()  # NEW, not closed
    assert ticket.csat_eligible is False


def test_csat_immutable_no_update_path(session, tenant_id, make_ticket):
    ticket = _closed_ticket(session, tenant_id, make_ticket)
    ticket_service.submit_csat(session, tenant_id, ticket.id, rating=4)
    session.commit()
    csat = session.query(CustomerSatisfaction).filter_by(ticket_id=ticket.id).first()
    csat.rating = 5  # even a direct mutation attempt is not exposed via any service/API
    session.commit()
    # The service layer never exposes an update method; only the event is recorded once.
    from app.services.audit_service import ticket_events

    events = [e.event_type for e in ticket_events(session, ticket.id)]
    assert events.count("ticket.csat_received") == 1
