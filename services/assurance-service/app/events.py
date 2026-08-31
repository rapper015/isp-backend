"""Assurance event contracts (transactional outbox / idempotent inbox)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import InboxMessage, OutboxEvent

EXCHANGE = "assurance.events.v1"

PUBLISHED_TOPOLOGY = {
    "assurance.alert_normalized.v1",
    "assurance.alert_resolved.v1",
    "assurance.incident_created.v1",
    "assurance.incident_updated.v1",
    "assurance.incident_resolved.v1",
    "assurance.customer_impact_detected.v1",
    "assurance.slo_at_risk.v1",
    "assurance.slo_breached.v1",
    "assurance.error_budget_exhausted.v1",
    "assurance.root_cause_hypothesis_created.v1",
    "assurance.root_cause_confirmed.v1",
    "assurance.postmortem_required.v1",
    "assurance.maintenance_window_approved.v1",
}

# Consumed domain events for change correlation + KPI/impact ingestion.
CONSUMED_EVENTS = {
    "oss.order.created.v1": "CHANGE",
    "oss.order.activated.v1": "PROVISIONING",
    "billing.payment.captured.v1": "PAYMENT",
    "billing.payment.failed.v1": "PAYMENT",
    "crm.customer.created.v1": "CUSTOMER",
    "crm.customer.activated.v1": "PROVISIONING",
    "ticket.created.v1": "SUPPORT",
    "ticket.resolved.v1": "SUPPORT",
    "workforce.job.completed.v1": "WORKFORCE",
    "device.cpe.offline.v1": "DEVICE",
    "device.cpe.online.v1": "DEVICE",
    "tenancy.tenant.provisioned.v1": "TENANT",
    "aaa.session.stale.v1": "NETWORK",
    "nas.health_changed.v1": "NETWORK",
    "firmware.rollout.started.v1": "CHANGE",
    "network.policy.changed.v1": "CHANGE",
    "network.policy.deployed.v1": "CHANGE",
    "configuration.profile.changed.v1": "CHANGE",
}

CONSUMED_ALIASES = {
    "billing.payment.captured.v1": ("billing", "payment.captured.v1"),
    "billing.payment.failed.v1": ("billing", "payment.failed.v1"),
    "crm.customer.activated.v1": ("crm", "customer.activated.v1"),
    "nas.health_changed.v1": ("aaa", "nas.health_changed.v1"),
    "aaa.session.stale.v1": ("aaa", "session.stale.v1"),
    "network.policy.changed.v1": ("aaa", "network.policy.changed.v1"),
    "device.cpe.offline.v1": ("device", "cpe.offline.v1"),
    "device.cpe.online.v1": ("device", "cpe.online.v1"),
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def envelope(event_type: str, tenant_id, payload: dict, *, correlation_id: str | None = None,
             causation_id: str | None = None, idempotency_key: str | None = None,
             producer: str = "assurance-service", trace_context: dict | None = None) -> dict:
    if event_type not in PUBLISHED_TOPOLOGY and event_type not in CONSUMED_EVENTS:
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
        "trace_context": trace_context or {},
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
                                .order_by(OutboxEvent.id).limit(limit)))


def consume_once(session: Session, event_id: str, consumer: str) -> bool:
    existing = session.scalars(select(InboxMessage).where(
        InboxMessage.consumer == consumer, InboxMessage.event_id == event_id)).first()
    if existing is not None:
        return False
    session.add(InboxMessage(consumer=consumer, event_id=event_id, event_type="",
                             received_at=_now()))
    session.flush()
    return True


def canonical_event_type(event_type: str) -> str:
    if event_type in CONSUMED_EVENTS:
        return event_type
    for canonical, (domain, alias) in CONSUMED_ALIASES.items():
        if event_type == alias:
            return canonical
    raise ValueError(f"event type {event_type!r} is not consumed by assurance-service")
