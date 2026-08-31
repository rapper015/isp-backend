"""Audit + event primitives for the support service.

- ``append_event``: appends an immutable TicketEvent to the ticket's stream
  with optimistic concurrency (aggregate_version). Historical events are never
  edited or deleted.
- ``outbox``: records a cross-service event in the transactional outbox.
- ``audit``: appends an immutable administrative audit row for configuration
  changes (SLA/catalog/routing/knowledge governance).
- ``correlation``: builds/keeps a correlation id.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..events import publish_outbox
from ..models import AuditLog, Ticket, TicketEvent


def correlation(value: str | None = None) -> str:
    return value or uuid.uuid4().hex


def append_event(
    session: Session,
    ticket: Ticket,
    event_type: str,
    *,
    payload: dict | None = None,
    actor_type: str | None = None,
    actor_id: str | None = None,
    correlation_id: str | None = None,
    causation_id: str | None = None,
    idempotency_key: str | None = None,
    event_metadata: dict | None = None,
) -> TicketEvent:
    """Append an immutable event to the ticket stream.

    The aggregate version is read from the current ticket and incremented;
    optimistic concurrency is enforced by the (ticket_id, aggregate_version)
    unique constraint. The caller commits the transaction."""
    ticket.aggregate_version += 1
    event = TicketEvent(
        tenant_id=ticket.tenant_id,
        ticket_id=ticket.id,
        aggregate_version=ticket.aggregate_version,
        event_type=event_type,
        event_version=1,
        schema_version=1,
        actor_type=actor_type,
        actor_id=actor_id,
        correlation_id=correlation_id,
        causation_id=causation_id,
        idempotency_key=idempotency_key,
        payload=payload or {},
        event_metadata=event_metadata or {},
    )
    session.add(event)
    return event


def outbox(
    session: Session,
    event_type: str,
    tenant_id,
    correlation_id: str | None,
    payload: dict | None = None,
    idempotency_key: str | None = None,
):
    return publish_outbox(
        session,
        event_type,
        payload or {},
        tenant_id=tenant_id,
        correlation_id=correlation_id,
        idempotency_key=idempotency_key,
    )


def audit(
    session: Session,
    tenant_id,
    event_type: str,
    entity_type: str | None,
    entity_id: str | None,
    *,
    actor: str | None = None,
    reason: str | None = None,
    correlation_id: str | None = None,
    safe_before: dict | None = None,
    safe_after: dict | None = None,
) -> AuditLog:
    row = AuditLog(
        tenant_id=tenant_id,
        event_type=event_type,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id else None,
        actor=actor,
        reason=reason,
        correlation_id=correlation_id,
        safe_before=safe_before or {},
        safe_after=safe_after or {},
    )
    session.add(row)
    return row


def ticket_events(session: Session, ticket_id) -> list[TicketEvent]:
    return list(
        session.scalars(
            select(TicketEvent)
            .where(TicketEvent.ticket_id == ticket_id)
            .order_by(TicketEvent.aggregate_version)
        )
    )
