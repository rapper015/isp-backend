"""Tenancy event contracts (RabbitMQ outbox/inbox). Runtime envelopes carry
tenant_id so consumers validate tenant before acting. Unknown event types raise
ValueError (callers ack-and-ignore only for subscribed types)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import InboxMessage, OutboxEvent

EXCHANGE = "tenancy.events.v1"

PUBLISHED_TOPOLOGY = {
    "tenancy.tenant.requested.v1",
    "tenancy.tenant.provisioned.v1",
    "tenancy.tenant.activated.v1",
    "tenancy.tenant.restricted.v1",
    "tenancy.tenant.suspended.v1",
    "tenancy.tenant.resumed.v1",
    "tenancy.tenant.offboarding_started.v1",
    "tenancy.tenant.archived.v1",
    "tenancy.partner.created.v1",
    "tenancy.partner.status_changed.v1",
    "tenancy.membership.changed.v1",
    "tenancy.role.changed.v1",
    "tenancy.feature.changed.v1",
    "tenancy.domain.changed.v1",
    "tenancy.impersonation.started.v1",
    "tenancy.commission.earning.v1",
    "tenancy.commission.clawback.v1",
    "tenancy.settlement.approved.v1",
    "tenancy.settlement.locked.v1",
    "tenancy.settlement.paid.v1",
    "tenancy.wallet.entry.v1",
    "tenancy.customer.transferred.v1",
    "tenancy.ownership.changed.v1",
    "tenancy.notification.sent.v1",
    "tenancy.campaign.scheduled.v1",
    "tenancy.campaign.executed.v1",
    "tenancy.usage.metered.v1",
    "tenancy.cost.recorded.v1",
    "tenancy.compliance.completed.v1",
    "tenancy.threat_hunt.completed.v1",
    "tenancy.service_chain.created.v1",
    "tenancy.insight.generated.v1",
    "tenancy.procurement.automated.v1",
    "tenancy.inventory_forecast.computed.v1",
    "tenancy.roi.recorded.v1",
    "tenancy.scaling_rule.applied.v1",
    "tenancy.mesh_link.established.v1",
    "tenancy.cloud.abstraction_registered.v1",
    "tenancy.workload.migrated.v1",
    # core-platform AI/governance (Master Spec Batch 8g)
    "tenancy.sentiment.analyzed.v1",
    "tenancy.reply.suggestion.generated.v1",
    "tenancy.leader.elected.v1",
    "tenancy.beta.released.v1",
    "tenancy.carbon.calculated.v1",
    "tenancy.intent.executed.v1",
    "tenancy.clause.extracted.v1",
    "tenancy.risk.detected.v1",
    "tenancy.strategy.suggested.v1",
    "tenancy.ethics.validated.v1",
    "tenancy.olt.simulated.v1",
    "tenancy.latency.simulated.v1",
}

# Commission basis events consumed from other services (idempotent).
CONSUMED_EVENTS = {
    "billing.payment.captured.v1": "PAYMENT_COLLECTION",
    "billing.payment.refunded.v1": "PAYMENT_REVERSAL",
    "crm.customer.activated.v1": "SERVICE_ACTIVATION",
    "oss.order.activated.v1": "SERVICE_ACTIVATION",
    "billing.invoice.issued.v1": "INVOICE_AMOUNT",
}

CONSUMED_ALIASES = {
    "billing.payment.captured.v1": ("billing", "payment.captured.v1"),
    "billing.payment.refunded.v1": ("billing", "payment.refunded.v1"),
    "crm.customer.activated.v1": ("crm", "customer.activated.v1"),
    "oss.order.activated.v1": ("oss", "order.activated.v1"),
    "billing.invoice.issued.v1": ("billing", "invoice.issued.v1"),
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def envelope(event_type: str, tenant_id, payload: dict, *, correlation_id: str | None = None,
             causation_id: str | None = None, idempotency_key: str | None = None,
             producer: str = "tenancy-service") -> dict:
    if event_type not in PUBLISHED_TOPOLOGY:
        raise ValueError(f"unknown event type {event_type!r}")
    return {
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "schema_version": 1,
        "occurred_at": _now().isoformat(),
        "published_at": None,
        "tenant_id": str(tenant_id) if tenant_id else None,
        "correlation_id": correlation_id,
        "causation_id": causation_id,
        "idempotency_key": idempotency_key or str(uuid.uuid4()),
        "producer": producer,
        "trace_context": {},
        "payload": payload,
    }


def outbox(session: Session, event_type: str, tenant_id, correlation_id: str | None,
           payload: dict, *, idempotency_key: str | None = None) -> OutboxEvent:
    if event_type not in PUBLISHED_TOPOLOGY:
        raise ValueError(f"unknown event type {event_type!r}")
    row = OutboxEvent(event_type=event_type, tenant_id=tenant_id,
                      correlation_id=correlation_id, idempotency_key=idempotency_key, payload=payload)
    session.add(row)
    session.flush()
    return row


def unprocessed_events(session: Session, limit: int = 100):
    return list(session.scalars(select(OutboxEvent).where(OutboxEvent.published_at.is_(None))
                                .order_by(OutboxEvent.created_at).limit(limit)))


def consume_once(session: Session, event_id: str, consumer: str) -> bool:
    existing = session.scalars(select(InboxMessage).where(
        InboxMessage.consumer == consumer, InboxMessage.event_id == event_id)).first()
    if existing is not None:
        return False
    session.add(InboxMessage(consumer=consumer, event_id=event_id,
                             event_type="", received_at=_now()))
    session.flush()
    return True


def canonical_event_type(event_type: str) -> str:
    if event_type in CONSUMED_EVENTS:
        return event_type
    for canonical, (domain, alias) in CONSUMED_ALIASES.items():
        if event_type == alias:
            return canonical
    raise ValueError(f"event type {event_type!r} is not consumed by tenancy-service")
