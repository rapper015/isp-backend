"""BSS event contracts + transactional outbox/inbox helpers."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import InboxMessage, OutboxEvent

EXCHANGE = "bss.events.v1"
SERVICE = "bss-service"

EVENTS = (
    # payment
    "payment.intent_created.v1",
    "payment.pending.v1",
    "payment.authorized.v1",
    "payment.captured.v1",
    "payment.failed.v1",
    "payment.allocated.v1",
    "payment.partially_allocated.v1",
    "payment.unallocated_credit_created.v1",
    "payment.refund_requested.v1",
    "payment.refunded.v1",
    "payment.disputed.v1",
    "payment.chargeback_received.v1",
    # settlement / reconciliation
    "settlement.received.v1",
    "reconciliation.completed.v1",
    "reconciliation.exception_created.v1",
    # billing / restoration
    "billing.account_delinquent.v1",
    "billing.suspension_required.v1",
    "billing.restoration_eligible.v1",
    # dunning
    "dunning.stage_changed.v1",
    "dunning.case_resolved.v1",
    # invoice
    "invoice.issued.v1",
    "invoice.overdue.v1",
)


def publish_outbox(session: Session, event_type: str, payload: dict, tenant_id=None, correlation_id=None, idempotency_key=None) -> OutboxEvent:
    if event_type not in EVENTS:
        raise ValueError(f"unknown BSS event type {event_type!r}")
    row = OutboxEvent(
        tenant_id=tenant_id,
        event_type=event_type,
        correlation_id=correlation_id or str(uuid.uuid4().hex),
        idempotency_key=idempotency_key,
        payload=payload,
    )
    session.add(row)
    return row


def consume_once(session: Session, event_id: str, consumer: str = "bss-order-handler") -> bool:
    existing = session.get(InboxMessage, (event_id, consumer))
    if existing is not None:
        return False
    session.add(InboxMessage(event_id=event_id, consumer=consumer))
    return True
