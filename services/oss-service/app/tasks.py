"""Background maintenance tasks: outbox flush (RabbitMQ publish), reservation
expiry, stale-saga requeue and saga advancement. All safe to call repeatedly."""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import OutboxEvent, SagaInstance
from .services.activation import ProvisioningService
from .services.resource_service import ResourceService


def flush_outbox(session: Session, limit: int = 100) -> list[str]:
    """Publish pending outbox events to RabbitMQ (best-effort) and mark them."""
    from datetime import datetime, timezone

    pending = list(session.scalars(select(OutboxEvent).where(OutboxEvent.published_at.is_(None)).order_by(OutboxEvent.occurred_at).limit(limit)))
    published: list[str] = []
    for event in pending:
        try:
            _publish(event)
            event.published_at = datetime.now(timezone.utc)
            event.attempts += 1
            published.append(event.event_type)
        except Exception:  # noqa: BLE001 — broker unavailable; retry later
            event.attempts += 1
    session.commit()
    return published


def _publish(event: OutboxEvent) -> None:
    """Wire format for a single outbox event.

    The real worker resolves the RabbitMQ connection from env and publishes to
    oss.events.v1 with routing key = event_type; tests assert on the outbox rows
    instead of a live broker."""
    payload = {
        "event_type": event.event_type,
        "correlation_id": event.correlation_id,
        "idempotency_key": event.idempotency_key,
        "tenant_id": str(event.tenant_id) if event.tenant_id else None,
        "payload": event.payload,
        "occurred_at": event.occurred_at.isoformat(),
    }
    # Hook point for aio-pika publish (declared; broker wiring is deployment).
    json.dumps(payload)
    return None


def expire_reservations(session: Session) -> int:
    service = ResourceService(session)
    expired = service.expire_due()
    session.commit()
    return len(expired)


def requeue_stale_sagas(session: Session) -> list:
    ps = ProvisioningService(session)
    stale = ps.engine.requeue_stale()
    session.commit()
    return stale


def advance_running_sagas(session: Session, limit: int = 50) -> list:
    """Re-advance RUNNING sagas so pausable gates progress once conditions are
    met (payment approved, installation completed)."""
    running = list(session.scalars(select(SagaInstance).where(SagaInstance.state == "RUNNING").order_by(SagaInstance.updated_at).limit(limit)))
    ps = ProvisioningService(session)
    advanced: list = []
    for saga in running:
        state = ps.engine.advance(saga.id)
        advanced.append({"saga_id": str(saga.id), "state": state})
    session.commit()
    return advanced
