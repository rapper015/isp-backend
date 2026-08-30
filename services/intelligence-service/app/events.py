"""Intelligence event contracts (transactional outbox / idempotent inbox)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import InboxMessage, OutboxEvent

EXCHANGE = "intelligence.events.v1"

PUBLISHED_TOPOLOGY = {
    "ai.dataset_ready.v1",
    "ai.data_quality_failed.v1",
    "ai.training_completed.v1",
    "ai.model_approved.v1",
    "ai.model_rejected.v1",
    "ai.model_deployed.v1",
    "ai.model_rolled_back.v1",
    "ai.prediction_created.v1",
    "ai.fraud_signal_detected.v1",
    "ai.churn_risk_updated.v1",
    "ai.failure_risk_detected.v1",
    "ai.capacity_risk_detected.v1",
    "ai.recommendation_created.v1",
    "ai.remediation_requested.v1",
    "ai.remediation_approved.v1",
    "ai.remediation_rejected.v1",
    "ai.remediation_started.v1",
    "ai.remediation_completed.v1",
    "ai.remediation_failed.v1",
    "ai.remediation_compensated.v1",
    "ai.model_drift_detected.v1",
    "ai.kill_switch_engaged.v1",
}

# Consumed domain events -> normalized analytical contracts.
CONSUMED_EVENTS = {
    "crm.customer.created.v1": "CUSTOMER",
    "crm.customer.activated.v1": "CUSTOMER",
    "crm.customer.lifecycle_changed.v1": "CUSTOMER",
    "crm.customer.risk_changed.v1": "CUSTOMER",
    "crm.lead.converted.v1": "ONBOARDING",
    "oss.order.created.v1": "ORDER",
    "oss.order.activated.v1": "ORDER",
    "oss.order.completed.v1": "ORDER",
    "oss.service.provisioned.v1": "PROVISIONING",
    "billing.invoice.issued.v1": "INVOICE",
    "billing.payment.captured.v1": "PAYMENT",
    "billing.payment.failed.v1": "PAYMENT",
    "billing.payment.refunded.v1": "PAYMENT",
    "billing.account_delinquent.v1": "PAYMENT",
    "billing.account.suspension_required.v1": "PAYMENT",
    "aaa.session.stale.v1": "SESSION",
    "aaa.session.established.v1": "SESSION",
    "aaa.session.terminated.v1": "SESSION",
    "nas.health_changed.v1": "NETWORK",
    "nas.radius_registration.v1": "NETWORK",
    "network.identity_assigned.v1": "NETWORK",
    "device.cpe.online.v1": "DEVICE",
    "device.cpe.offline.v1": "DEVICE",
    "device.cpe.firmware_changed.v1": "DEVICE",
    "device.cpe.diagnostics.v1": "DEVICE",
    "tenancy.tenant.provisioned.v1": "TENANT",
    "assurance.alert_normalized.v1": "OBSERVABILITY",
    "assurance.alert_resolved.v1": "OBSERVABILITY",
    "assurance.incident_created.v1": "OBSERVABILITY",
    "assurance.incident_resolved.v1": "OBSERVABILITY",
    "assurance.customer_impact_detected.v1": "OBSERVABILITY",
    "assurance.slo_at_risk.v1": "OBSERVABILITY",
    "assurance.slo_breached.v1": "OBSERVABILITY",
    "assurance.error_budget_exhausted.v1": "OBSERVABILITY",
}

CONSUMED_ALIASES = {
    "billing.payment.captured.v1": ("billing", "payment.captured.v1"),
    "billing.payment.failed.v1": ("billing", "payment.failed.v1"),
    "nas.health_changed.v1": ("aaa", "nas.health_changed.v1"),
    "aaa.session.stale.v1": ("aaa", "session.stale.v1"),
    "device.cpe.offline.v1": ("device", "cpe.offline.v1"),
    "device.cpe.online.v1": ("device", "cpe.online.v1"),
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def envelope(event_type: str, tenant_id, payload: dict, *, correlation_id: str | None = None,
             causation_id: str | None = None, idempotency_key: str | None = None,
             producer: str = "intelligence-service", trace_context: dict | None = None) -> dict:
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
    raise ValueError(f"event type {event_type!r} is not consumed by intelligence-service")
