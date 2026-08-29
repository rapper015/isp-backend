"""Correlation IDs, immutable device timeline events, outbox publication and
administrative audit."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..events import publish_outbox
from ..models import AuditLog, ManagedCpe

# Managed CPE timeline event table (created below after import to avoid cycles)
_CPE_EVENT_MODEL = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def correlation(value: str | None = None) -> str:
    return value or uuid.uuid4().hex


def cpe_event_model():
    global _CPE_EVENT_MODEL
    if _CPE_EVENT_MODEL is None:
        from ..models.identity import CpeEvent

        _CPE_EVENT_MODEL = CpeEvent
    return _CPE_EVENT_MODEL


def append_event(session: Session, cpe: ManagedCpe, event_type: str, *, payload: dict | None = None,
                 actor_type: str = "system", actor_id: str | None = None,
                 correlation_id: str | None = None, causation_id: str | None = None) -> None:
    """Bumps the aggregate version and appends an immutable timeline event."""
    cpe.aggregate_version += 1
    model = cpe_event_model()
    session.add(model(
        cpe_id=cpe.id, tenant_id=cpe.tenant_id, version=cpe.aggregate_version,
        event_type=event_type, payload=payload or {}, actor_type=actor_type, actor_id=actor_id,
        correlation_id=correlation_id or cpe.correlation_id, causation_id=causation_id))
    session.flush()


def outbox(session: Session, event_type: str, tenant_id, correlation_id: str | None,
           payload: dict, *, causation_id: str | None = None, idempotency_key: str | None = None):
    return publish_outbox(session, event_type, tenant_id, correlation_id, payload,
                          causation_id=causation_id, idempotency_key=idempotency_key)


def audit(session: Session, tenant_id, event_type: str, entity_type: str | None = None,
          entity_id: str | None = None, *, actor: str | None = None, reason: str | None = None,
          payload: dict | None = None, correlation_id: str | None = None) -> AuditLog:
    row = AuditLog(tenant_id=tenant_id, event_type=event_type, entity_type=entity_type, entity_id=entity_id,
                   actor=actor, reason=reason, payload=payload or {})
    session.add(row)
    session.flush()
    return row


def cpe_events(session: Session, cpe_id) -> list:
    return list(session.scalars(
        select(cpe_event_model()).where(cpe_event_model().cpe_id == cpe_id)
        .order_by(cpe_event_model().version)))


def cpe_event_by_id(session: Session, event_id: uuid.UUID):
    return session.get(cpe_event_model(), event_id)
