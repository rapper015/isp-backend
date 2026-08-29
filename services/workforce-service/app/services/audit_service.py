"""Audit + event primitives for the workforce service.

- ``append_event`` appends an immutable WorkOrderEvent with optimistic
  concurrency (aggregate_version).
- ``outbox`` records a cross-service event in the transactional outbox.
- ``audit`` appends an immutable administrative audit row.
- ``correlation`` builds/keeps a correlation id.
"""
from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from ..events import publish_outbox
from ..models import AuditLog, WorkOrder, WorkOrderEvent


def correlation(value: str | None = None) -> str:
    return value or uuid.uuid4().hex


def append_event(
    session: Session,
    work_order: WorkOrder,
    event_type: str,
    *,
    payload: dict | None = None,
    actor_type: str | None = None,
    actor_id: str | None = None,
    correlation_id: str | None = None,
    causation_id: str | None = None,
    idempotency_key: str | None = None,
    event_metadata: dict | None = None,
) -> WorkOrderEvent:
    work_order.aggregate_version += 1
    event = WorkOrderEvent(
        tenant_id=work_order.tenant_id,
        work_order_id=work_order.id,
        aggregate_version=work_order.aggregate_version,
        event_type=event_type,
        event_version=1,
        schema_version=1,
        actor_type=actor_type,
        actor_id=actor_id,
        correlation_id=correlation_id,
        causation_id=causation_id,
        idempotency_key=idempotency_key,
        payload=payload or {},
        event_metadata=event_metadata or {},
    )
    session.add(event)
    return event


def outbox(session: Session, event_type: str, tenant_id, correlation_id: str | None,
           payload: dict | None = None, idempotency_key: str | None = None):
    return publish_outbox(session, event_type, payload or {}, tenant_id=tenant_id,
                          correlation_id=correlation_id, idempotency_key=idempotency_key)


def audit(session: Session, tenant_id, event_type: str, entity_type: str | None, entity_id,
          *, actor: str | None = None, reason: str | None = None, correlation_id: str | None = None,
          safe_before: dict | None = None, safe_after: dict | None = None) -> AuditLog:
    row = AuditLog(
        tenant_id=tenant_id, event_type=event_type, entity_type=entity_type,
        entity_id=str(entity_id) if entity_id else None, actor=actor, reason=reason,
        correlation_id=correlation_id, safe_before=safe_before or {}, safe_after=safe_after or {},
    )
    session.add(row)
    return row


def work_order_events(session: Session, work_order_id) -> list[WorkOrderEvent]:
    from sqlalchemy import select

    return list(session.scalars(
        select(WorkOrderEvent).where(WorkOrderEvent.work_order_id == work_order_id)
        .order_by(WorkOrderEvent.aggregate_version)))
