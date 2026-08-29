"""Workforce event naming + transactional outbox/inbox helpers.

The outbox table is the durable record; a worker publishes to RabbitMQ on
`workforce.events.v1`. The inbox guarantees at-least-once delivery with
idempotent processing. Domain events never carry proof files or unnecessary PII."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import InboxMessage, OutboxEvent

EXCHANGE = "workforce.events.v1"
RETRY_EXCHANGE = "workforce.retry.v1"
DEAD_LETTER_EXCHANGE = "workforce.dead.v1"
SERVICE = "workforce-service"

PUBLISHED_TOPOLOGY = {
    "work_order": [
        "workforce.work_order.created.v1",
        "workforce.work_order.validated.v1",
        "workforce.work_order.scheduled.v1",
        "workforce.work_order.assigned.v1",
        "workforce.work_order.assignment_accepted.v1",
        "workforce.work_order.dispatched.v1",
        "workforce.work_order.technician_arrived.v1",
        "workforce.work_order.execution_started.v1",
        "workforce.work_order.blocked.v1",
        "workforce.work_order.remote_action_requested.v1",
        "workforce.work_order.execution_completed.v1",
        "workforce.work_order.qa_approved.v1",
        "workforce.work_order.qa_rejected.v1",
        "workforce.work_order.completed.v1",
        "workforce.work_order.failed.v1",
        "workforce.work_order.cancelled.v1",
        "workforce.work_order.sla_at_risk.v1",
        "workforce.work_order.sla_breached.v1",
        "workforce.work_order.appointment_confirmed.v1",
        "workforce.work_order.appointment_rescheduled.v1",
        "workforce.appointment.confirmation_requested.v1",
        "workforce.work_order.checkin.v1",
        "workforce.work_order.checkout.v1",
    ],
    "inventory": [
        "workforce.inventory.device_installed.v1",
        "workforce.inventory.device_recovered.v1",
        "workforce.inventory.material_used.v1",
    ],
}

CONSUMED_EVENTS = {
    "oss.order.field_work_required.v1",
    "oss.order.provisioning_ready.v1",
    "support.ticket.field_visit_requested.v1",
    "nms.repair_required.v1",
    "inventory.reservation_confirmed.v1",
    "oss.service.activation_completed.v1",
    "oss.service.activation_failed.v1",
    "crm.customer.updated.v1",
    "workforce.appointment.customer_confirmed.v1",
}

CONSUMED_ALIASES = {
    "order.field_work_required.v1": "oss.order.field_work_required.v1",
    "order.provisioning_ready.v1": "oss.order.provisioning_ready.v1",
    "ticket.field_visit_requested.v1": "support.ticket.field_visit_requested.v1",
    "repair_required.v1": "nms.repair_required.v1",
    "reservation_confirmed.v1": "inventory.reservation_confirmed.v1",
    "service.activation_completed.v1": "oss.service.activation_completed.v1",
    "service.activation_failed.v1": "oss.service.activation_failed.v1",
    "customer.updated.v1": "crm.customer.updated.v1",
    "appointment.customer_confirmed.v1": "workforce.appointment.customer_confirmed.v1",
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


def consume_once(session: Session, event_id: str, consumer: str = "workforce-handler") -> bool:
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
