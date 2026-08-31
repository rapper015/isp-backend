"""Event topology, transactional outbox and idempotent consumer inbox for the
device-management service. GenieACS session data is never the platform's event
source — the platform publishes normalized `cpe.*` events."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import InboxMessage, OutboxEvent

EXCHANGE = "device.events.v1"
SERVICE = "device-management-service"

PUBLISHED_TOPOLOGY = {
    "cpe": [
        "cpe.discovered.v1",
        "cpe.quarantined.v1",
        "cpe.claimed.v1",
        "cpe.online.v1",
        "cpe.offline.v1",
        "cpe.assigned.v1",
        "cpe.configuration_requested.v1",
        "cpe.configuration_applied.v1",
        "cpe.configuration_failed.v1",
        "cpe.configuration_drift_detected.v1",
        "cpe.diagnostic_completed.v1",
        "cpe.firmware_upgrade_started.v1",
        "cpe.firmware_upgrade_completed.v1",
        "cpe.firmware_upgrade_failed.v1",
        "cpe.rebooted.v1",
        "cpe.replacement_required.v1",
        "cpe.decommissioned.v1",
    ],
}

CONSUMED_EVENTS = {
    "inventory.device_reserved.v1",
    "inventory.device_installed.v1",
    "inventory.device_recovered.v1",
    "work_order.device_installed.v1",
    "order.cpe_provisioning_requested.v1",
    "service.activated.v1",
    "service.plan_changed.v1",
    "ticket.device_diagnostic_requested.v1",
    "nms.device_investigation_requested.v1",
}

CONSUMED_ALIASES = {
    "device_reserved.v1": "inventory.device_reserved.v1",
    "device_installed.v1": "inventory.device_installed.v1",
    "device_recovered.v1": "inventory.device_recovered.v1",
    "cpe_provisioning_requested.v1": "order.cpe_provisioning_requested.v1",
    "device_diagnostic_requested.v1": "ticket.device_diagnostic_requested.v1",
    "device_investigation_requested.v1": "nms.device_investigation_requested.v1",
}

ALL_EVENT_TYPES = {t for ts in PUBLISHED_TOPOLOGY.values() for t in ts}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def canonical_event_type(event_type: str) -> str:
    if event_type in CONSUMED_EVENTS:
        return event_type
    if event_type in CONSUMED_ALIASES:
        return CONSUMED_ALIASES[event_type]
    for ts in PUBLISHED_TOPOLOGY.values():
        if event_type in ts:
            return event_type
    raise ValueError(f"unknown event type {event_type!r}")


def publish_outbox(session: Session, event_type: str, tenant_id, correlation_id: str | None,
                   payload: dict, *, causation_id: str | None = None,
                   idempotency_key: str | None = None) -> OutboxEvent:
    if event_type not in ALL_EVENT_TYPES:
        raise ValueError(f"unknown event type {event_type!r}")
    event = OutboxEvent(tenant_id=tenant_id, event_type=event_type, correlation_id=correlation_id,
                        causation_id=causation_id, idempotency_key=idempotency_key, payload=payload)
    session.add(event)
    session.flush()
    return event


def consume_once(session: Session, event_id: str, consumer: str = "device-management-handler") -> bool:
    """Return True when the event has not been processed by this consumer yet."""
    if session.get(InboxMessage, (str(event_id), consumer)) is not None:
        return False
    session.add(InboxMessage(event_id=str(event_id), consumer=consumer))
    session.flush()
    return True


def unprocessed_events(session: Session, limit: int = 200) -> list[OutboxEvent]:
    return list(session.scalars(
        select(OutboxEvent).where(OutboxEvent.published_at.is_(None)).order_by(OutboxEvent.occurred_at).limit(limit)))


def envelope(event: OutboxEvent) -> dict:
    return {
        "id": str(event.id),
        "event_type": event.event_type,
        "schema_version": 1,
        "occurred_at": event.occurred_at.isoformat() if event.occurred_at else _now().isoformat(),
        "published_at": _now().isoformat(),
        "tenant_id": str(event.tenant_id) if event.tenant_id else None,
        "correlation_id": event.correlation_id,
        "causation_id": event.causation_id,
        "idempotency_key": event.idempotency_key,
        "producer": SERVICE,
        "payload": event.payload,
    }
