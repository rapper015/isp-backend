"""Support event naming + transactional outbox/inbox helpers (Milestone 5).

The outbox table is the durable record; a worker publishes to RabbitMQ on
`support.events.v1`. The inbox guarantees at-least-once delivery with
idempotent processing. Domain events never carry full ticket bodies, private
attachments or unnecessary PII.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import InboxMessage, OutboxEvent

EXCHANGE = "support.events.v1"
RETRY_EXCHANGE = "support.retry.v1"
DEAD_LETTER_EXCHANGE = "support.dead.v1"
SERVICE = "support-service"

# Events published by the support service.
PUBLISHED_TOPOLOGY = {
    "ticket": [
        "support.ticket.created.v1",
        "support.ticket.assigned.v1",
        "support.ticket.priority_changed.v1",
        "support.ticket.escalated.v1",
        "support.ticket.customer_replied.v1",
        "support.ticket.public_reply.v1",
        "support.ticket.sla_at_risk.v1",
        "support.ticket.sla_breached.v1",
        "support.ticket.support_action_requested.v1",
        "support.ticket.support_action_completed.v1",
        "support.ticket.resolved.v1",
        "support.ticket.closed.v1",
        "support.ticket.reopened.v1",
        "support.ticket.csat_received.v1",
        "support.ticket.outage_linked.v1",
        "support.ticket.oss_order_linked.v1",
        "support.ticket.workforce_job_linked.v1",
    ],
    "problem": ["support.problem.created.v1"],
    "major_incident": ["support.major_incident.declared.v1"],
}

# Events the support service consumes from other bounded contexts.
CONSUMED_EVENTS = {
    "crm.customer.updated.v1",
    "oss.service.activated.v1",
    "oss.service.suspended.v1",
    "oss.service.reactivated.v1",
    "oss.service.terminated.v1",
    "oss.order.completed.v1",
    "oss.order.failed.v1",
    "bss.payment.captured.v1",
    "bss.billing.account_delinquent.v1",
    "aaa.session.started.v1",
    "aaa.session.stopped.v1",
    "nms.incident_created.v1",
    "nms.outage_detected.v1",
    "nms.outage_cleared.v1",
    "workforce.job_completed.v1",
}

# Accept aliases used by consumers so test fixtures can publish consistent events.
CONSUMED_ALIASES = {
    "payment.captured.v1": "bss.payment.captured.v1",
    "billing.account_delinquent.v1": "bss.billing.account_delinquent.v1",
    "session.started.v1": "aaa.session.started.v1",
    "session.stopped.v1": "aaa.session.stopped.v1",
    "incident_created.v1": "nms.incident_created.v1",
    "outage_detected.v1": "nms.outage_detected.v1",
    "outage_cleared.v1": "nms.outage_cleared.v1",
    "order.completed.v1": "oss.order.completed.v1",
    "order.failed.v1": "oss.order.failed.v1",
    "job_completed.v1": "workforce.job_completed.v1",
}

ALL_EVENT_TYPES = {t for ts in PUBLISHED_TOPOLOGY.values() for t in ts}


def canonical_event_type(event_type: str) -> str:
    return CONSUMED_ALIASES.get(event_type, event_type)


def publish_outbox(
    session: Session,
    event_type: str,
    payload: dict,
    tenant_id: uuid.UUID | None = None,
    correlation_id: str | None = None,
    idempotency_key: str | None = None,
) -> OutboxEvent:
    if event_type not in ALL_EVENT_TYPES:
        raise ValueError(f"unknown event type {event_type!r}")
    row = OutboxEvent(
        tenant_id=tenant_id,
        event_type=event_type,
        correlation_id=correlation_id,
        idempotency_key=idempotency_key,
        payload=payload,
    )
    session.add(row)
    return row


def consume_once(session: Session, event_id: str, consumer: str = "support-handler") -> bool:
    """Returns True if this (event, consumer) has not been processed yet.

    Idempotent: a duplicate delivery returns False and no handler work runs."""
    existing = session.get(InboxMessage, (event_id, consumer))
    if existing is not None:
        return False
    session.add(InboxMessage(event_id=event_id, consumer=consumer))
    return True


def unprocessed_events(session: Session) -> list[OutboxEvent]:
    return list(
        session.scalars(
            select(OutboxEvent)
            .where(OutboxEvent.published_at.is_(None))
            .order_by(OutboxEvent.occurred_at)
            .limit(200)
        )
    )
