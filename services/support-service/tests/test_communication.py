"""Communication tests: public/internal separation, HTML sanitization,
inbound deduplication, reply tokens and portal threading."""
import pytest

from app.domain.exceptions import DuplicateError
from app.services import communication_service, ticket_service


def test_public_reply_sanitizes_html(session, tenant_id, make_ticket):
    ticket = make_ticket()
    comment = communication_service.add_comment(
        session, tenant_id, ticket, kind="PUBLIC_REPLY", body='Hello <script>alert(1)</script> <b>world</b>',
        sender_type="AGENT", sender_id="a1")
    session.commit()
    assert "<script" not in comment.sanitized_body
    assert "<b>world</b>" in comment.sanitized_body
    assert comment.visibility == "PUBLIC"
    assert comment.reply_token is not None


def test_internal_note_never_exposed_in_public_view(session, tenant_id, make_ticket):
    ticket = make_ticket()
    communication_service.add_comment(session, tenant_id, ticket, kind="INTERNAL_NOTE",
                                      body="internal: customer is angry", visibility="INTERNAL",
                                      sender_type="AGENT", sender_id="a1")
    session.commit()
    all_comments = communication_service.comments_for_ticket(session, tenant_id, ticket.id, include_internal=True)
    public_comments = communication_service.comments_for_ticket(session, tenant_id, ticket.id, include_internal=False)
    assert len(all_comments) == 1
    assert len(public_comments) == 0


def test_inbound_dedupe_by_provider_message_id(session, tenant_id, make_ticket):
    ticket = make_ticket()
    communication_service.add_comment(session, tenant_id, ticket, kind="CUSTOMER_MESSAGE", direction="INBOUND",
                                      body="hello", channel="EMAIL", sender_type="CUSTOMER", sender_id="c",
                                      provider_message_id="msg-1")
    session.commit()
    with pytest.raises(DuplicateError):
        communication_service.add_comment(session, tenant_id, ticket, kind="CUSTOMER_MESSAGE", direction="INBOUND",
                                          body="hello again", channel="EMAIL", sender_type="CUSTOMER", sender_id="c",
                                          provider_message_id="msg-1")


def test_reply_token_roundtrip(session, tenant_id, make_ticket):
    ticket = make_ticket()
    comment = communication_service.add_comment(session, tenant_id, ticket, kind="PUBLIC_REPLY",
                                                body="please provide more info", sender_type="AGENT", sender_id="a1")
    session.commit()
    found = communication_service.ticket_by_reply_token(session, tenant_id, comment.reply_token)
    assert found is not None
    assert found.ticket_id == ticket.id


def test_customer_reply_generates_event_and_outbox(session, tenant_id, make_ticket):
    ticket = make_ticket()
    communication_service.add_comment(session, tenant_id, ticket, kind="CUSTOMER_MESSAGE", direction="INBOUND",
                                      body="it's still down", channel="CUSTOMER_PORTAL",
                                      sender_type="CUSTOMER", sender_id=ticket.customer_id)
    session.commit()
    from app.services.audit_service import ticket_events
    from app.models import OutboxEvent
    from sqlalchemy import select

    events = [e.event_type for e in ticket_events(session, ticket.id)]
    assert "ticket.customer_replied" in events
    outbox_types = list(session.scalars(select(OutboxEvent.event_type).where(OutboxEvent.tenant_id == tenant_id)))
    assert "support.ticket.customer_replied.v1" in outbox_types


def test_comment_requires_body(session, tenant_id, make_ticket):
    ticket = make_ticket()
    with pytest.raises(Exception):
        communication_service.add_comment(session, tenant_id, ticket, kind="PUBLIC_REPLY", body="   ",
                                          sender_type="AGENT", sender_id="a1")
