"""Background maintenance tasks: outbox flush (RabbitMQ publish), reservation
expiry, stale-saga requeue and saga advancement. All safe to call repeatedly."""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import OutboxEvent, SagaInstance
from .services.activation import ProvisioningService
from .services.resource_service import ResourceService


def flush_outbox(session: Session, limit: int = 100):
    from isp_shared.outbox import flush
    from .models import OutboxEvent
    delivered = flush(session, OutboxEvent, "oss.events.v1", limit=limit)
    return delivered


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
