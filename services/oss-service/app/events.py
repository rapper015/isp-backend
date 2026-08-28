"""OSS event naming + transactional outbox/inbox helpers.

The outbox table is the durable record; a worker publishes to RabbitMQ. Inbox
guarantees at-least-once delivery with idempotent processing.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import OutboxEvent, InboxMessage

EXCHANGE = "oss.events.v1"
SERVICE = "oss-service"

TOPOLOGY = {
    "order": [
        "oss.order.created.v1",
        "oss.order.submitted.v1",
        "oss.order.validated.v1",
        "oss.order.validation_failed.v1",
        "oss.order.payment_pending.v1",
        "oss.order.ready_for_fulfilment.v1",
        "oss.order.resources_reserved.v1",
        "oss.order.provisioning_started.v1",
        "oss.order.provisioning_completed.v1",
        "oss.order.verification_started.v1",
        "oss.order.completed.v1",
        "oss.order.failed.v1",
        "oss.order.compensation_started.v1",
        "oss.order.rolled_back.v1",
        "oss.order.cancelled.v1",
        "oss.order.cancellation_requested.v1",
        "oss.order.manual_intervention_required.v1",
        "oss.order.state_changed.v1",
        "oss.order.resumed.v1",
    ],
    "service": [
        "oss.service.created.v1",
        "oss.service.activated.v1",
        "oss.service.suspended.v1",
        "oss.service.reactivated.v1",
        "oss.service.terminated.v1",
        "oss.service.activation_failed.v1",
    ],
    "resource": [
        "oss.resource.reserved.v1",
        "oss.resource.allocated.v1",
        "oss.resource.released.v1",
        "oss.resource.expired.v1",
        "oss.resource.quarantined.v1",
    ],
    "workflow": [
        "oss.workflow.started.v1",
        "oss.workflow.step_completed.v1",
        "oss.workflow.step_failed.v1",
        "oss.workflow.compensating.v1",
        "oss.workflow.completed.v1",
        "oss.workflow.failed.v1",
        "oss.workflow.manual_intervention.v1",
    ],
}


def publish_outbox(
    session: Session,
    event_type: str,
    payload: dict,
    tenant_id: uuid.UUID | None = None,
    correlation_id: str | None = None,
    idempotency_key: str | None = None,
) -> OutboxEvent:
    if event_type not in {t for ts in TOPOLOGY.values() for t in ts}:
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


def consume_once(session: Session, event_id: str, consumer: str = "oss-order-handler") -> bool:
    """Returns True if this (event, consumer) has not been processed yet."""
    existing = session.get(InboxMessage, (event_id, consumer))
    if existing is not None:
        return False
    session.add(InboxMessage(event_id=event_id, consumer=consumer))
    return True
