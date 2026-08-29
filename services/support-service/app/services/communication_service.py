"""Communication service: public replies, internal notes, inbound customer
messages, HTML sanitization, inbound deduplication and secure reply tokens.

Internal notes are never exposed through customer-facing APIs (enforced by the
portal selector, not by the storage layer). Inbound messages are deduplicated
by provider message id so a repeated email/WhatsApp delivery cannot create
duplicate tickets or comments."""
from __future__ import annotations

import html as _html
import secrets
import uuid
from html.parser import HTMLParser

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.exceptions import DuplicateError, ValidationError
from ..enums import COMMENT_DIRECTIONS, COMMENT_KINDS, COMMENT_VISIBILITIES, SOURCE_CHANNELS
from ..models import Ticket, TicketComment
from ..services.audit_service import append_event, correlation, outbox

_ALLOWED_TAGS = {"b", "i", "u", "em", "strong", "a", "ul", "ol", "li", "p", "br", "code", "pre", "blockquote", "h1", "h2", "h3", "h4"}


class _Sanitizer(HTMLParser):
    def __init__(self):
        super().__init__()
        self.out = []

    def handle_starttag(self, tag, attrs):
        if tag in _ALLOWED_TAGS:
            filtered = [(k, v) for k, v in attrs if k == "href" and v.startswith(("http://", "https://", "mailto:"))]
            attrs_str = "".join(f' {k}="{_html.escape(v)}"' for k, v in filtered)
            self.out.append(f"<{tag}{attrs_str}>")

    def handle_endtag(self, tag):
        if tag in _ALLOWED_TAGS:
            self.out.append(f"</{tag}>")

    def handle_data(self, data):
        self.out.append(_html.escape(data))


def sanitize_html(content: str) -> str:
    parser = _Sanitizer()
    try:
        parser.feed(content)
        return "".join(parser.out)
    except Exception:  # noqa: BLE001 — never let malformed input break a reply
        return _html.escape(content)


def generate_reply_token() -> str:
    return secrets.token_urlsafe(24)


def add_comment(
    session: Session,
    tenant_id,
    ticket: Ticket,
    *,
    kind: str,
    body: str,
    direction: str = "OUTBOUND",
    channel: str = "CUSTOMER_PORTAL",
    visibility: str = "PUBLIC",
    sender_type: str | None = None,
    sender_id: str | None = None,
    recipient_reference: str | None = None,
    provider_message_id: str | None = None,
    correlation_id: str | None = None,
    auto: bool = False,
) -> TicketComment:
    kind = kind.upper()
    direction = direction.upper()
    channel = channel.upper()
    visibility = visibility.upper()
    if kind not in COMMENT_KINDS:
        raise ValidationError(f"invalid comment kind {kind!r}")
    if direction not in COMMENT_DIRECTIONS:
        raise ValidationError(f"invalid comment direction {direction!r}")
    if visibility not in COMMENT_VISIBILITIES:
        raise ValidationError(f"invalid comment visibility {visibility!r}")
    if channel not in SOURCE_CHANNELS:
        raise ValidationError(f"invalid comment channel {channel!r}")
    if not body or not body.strip():
        raise ValidationError("comment body is required")

    request_id = correlation(correlation_id)
    if provider_message_id:
        existing = session.scalars(
            select(TicketComment).where(TicketComment.tenant_id == tenant_id,
                                        TicketComment.provider_message_id == provider_message_id)
        ).first()
        if existing is not None:
            raise DuplicateError("message already ingested")

    sanitized = sanitize_html(body)
    reply_token = generate_reply_token() if kind == "PUBLIC_REPLY" else None
    comment = TicketComment(
        tenant_id=tenant_id, ticket_id=ticket.id, direction=direction, channel=channel, kind=kind,
        visibility=visibility, body=body.strip(), sanitized_body=sanitized,
        sender_type=sender_type, sender_id=sender_id, recipient_reference=recipient_reference,
        provider_message_id=provider_message_id, reply_token=reply_token, correlation_id=request_id,
    )
    session.add(comment)
    session.flush()

    event_type = {
        "PUBLIC_REPLY": "ticket.public_reply_sent",
        "CUSTOMER_MESSAGE": "ticket.customer_replied",
        "INTERNAL_NOTE": "ticket.internal_note_added",
        "SYSTEM_EVENT": "ticket.system_event",
        "AUTOMATED_NOTIFICATION": "ticket.automated_notification",
        "DIAGNOSTIC_RESULT": "ticket.diagnostic_result",
        "ACTION_RESULT": "ticket.action_result",
    }[kind]
    append_event(session, ticket, event_type,
                 payload={"comment_id": str(comment.id), "channel": channel, "visibility": visibility},
                 actor_type="customer" if sender_type == "CUSTOMER" else "agent",
                 actor_id=sender_id or (ticket.customer_id if sender_type == "CUSTOMER" else None),
                 correlation_id=request_id)
    if kind == "CUSTOMER_MESSAGE":
        outbox(session, "support.ticket.customer_replied.v1", tenant_id, request_id,
               {"ticket_id": str(ticket.id), "ticket_number": ticket.ticket_number, "channel": channel})
    elif kind == "PUBLIC_REPLY":
        outbox(session, "support.ticket.public_reply.v1", tenant_id, request_id,
               {"ticket_id": str(ticket.id), "ticket_number": ticket.ticket_number, "channel": channel})
    return comment


def comments_for_ticket(session: Session, tenant_id, ticket_id: uuid.UUID, *, include_internal: bool = True) -> list[TicketComment]:
    stmt = select(TicketComment).where(TicketComment.tenant_id == tenant_id, TicketComment.ticket_id == ticket_id)
    if not include_internal:
        stmt = stmt.where(TicketComment.visibility == "PUBLIC")
    return list(session.scalars(stmt.order_by(TicketComment.created_at)))


def ticket_by_reply_token(session: Session, tenant_id, reply_token: str) -> TicketComment | None:
    return session.scalars(
        select(TicketComment).where(TicketComment.reply_token == reply_token,
                                    TicketComment.tenant_id == tenant_id)
    ).first()
