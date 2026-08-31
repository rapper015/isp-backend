"""Ticket numbering: format, tenant isolation, monotonicity, immutability and
no-reuse-after-cancellation."""
import re

from app.models import Tenant
from app.services import ticket_service


def test_number_format(make_ticket):
    ticket = make_ticket()
    assert re.match(r"^TKT-\d{4}-\d{8}$", ticket.ticket_number)
    year = ticket.ticket_number.split("-")[1]
    assert year.isdigit() and len(year) == 4


def test_numbers_are_monotonic_and_increment(make_ticket):
    a = make_ticket()
    b = make_ticket()
    a_seq = int(a.ticket_number.split("-")[2])
    b_seq = int(b.ticket_number.split("-")[2])
    assert b_seq == a_seq + 1


def test_numbers_isolated_per_tenant(session, tenant_id, make_ticket):
    a = make_ticket()  # tenant_id sequence starts at 1
    t2 = Tenant(name="Second ISP", code="SECOND")
    session.add(t2)
    session.commit()
    session.refresh(t2)
    b = ticket_service.create_ticket(
        session, t2.id, ticket_type="INQUIRY", subject="Second tenant", description="desc",
        customer_id="C2", source_channel="PHONE")
    session.commit()
    c = ticket_service.create_ticket(
        session, t2.id, ticket_type="INQUIRY", subject="Second tenant 2", description="desc2",
        customer_id="C2", source_channel="PHONE")
    session.commit()
    # Each tenant's sequence is independent (both start at 1; tenant2 advances on its own).
    assert int(a.ticket_number.split("-")[2]) == 1
    assert int(b.ticket_number.split("-")[2]) == 1
    assert int(c.ticket_number.split("-")[2]) == 2


def test_number_never_reused_after_cancellation(session, tenant_id, make_ticket):
    first = make_ticket()
    number = first.ticket_number
    ticket_service.cancel(session, tenant_id, first.id, reason="test cancel")
    session.commit()
    second = make_ticket()
    assert second.ticket_number != number
    assert int(second.ticket_number.split("-")[2]) > int(number.split("-")[2])


def test_number_is_immutable_across_commands(session, tenant_id, make_ticket):
    ticket = make_ticket()
    number = ticket.ticket_number
    ticket_service.assign(session, tenant_id, ticket.id, agent_id="agent-1", actor="test")
    session.commit()
    assert ticket.ticket_number == number
