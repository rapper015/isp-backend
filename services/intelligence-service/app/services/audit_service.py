"""Audit + correlation helpers (append-only)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..events import outbox as _outbox
from ..models import AuditLog


def _now() -> datetime:
    return datetime.now(timezone.utc)


def correlation(correlation_id: str | None = None) -> str:
    return correlation_id or str(uuid.uuid4())


def audit(session: Session, tenant_id, actor: str | None, action: str, *, resource_type: str | None = None,
          resource_id: str | None = None, before: dict | None = None, after: dict | None = None,
          reason: str | None = None, correlation_id: str | None = None, metadata: dict | None = None) -> AuditLog:
    row = AuditLog(
        tenant_id=tenant_id, actor=actor, action=action, resource_type=resource_type,
        resource_id=str(resource_id) if resource_id else None,
        before_ref=before or {}, after_ref=after or {}, reason=reason,
        correlation_id=correlation_id, audit_metadata=metadata or {}, occurred_at=_now())
    session.add(row)
    session.flush()
    return row


def outbox(session: Session, event_type: str, tenant_id, correlation_id: str | None,
           payload: dict, *, idempotency_key: str | None = None):
    return _outbox(session, event_type, tenant_id, correlation_id, payload,
                   idempotency_key=idempotency_key)
