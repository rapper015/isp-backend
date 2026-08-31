"""CRM cross-cutting services: correlation ids, outbox publishing, audit log and
timeline entries. Secrets, identity numbers and document contents are never
written into these records."""
from __future__ import annotations

from uuid import uuid4

from sqlalchemy.orm import Session

from ..models import AuditLog, OutboxEvent, TimelineEntry


def correlation(value: str | None = None) -> str:
    return value or uuid4().hex


def outbox(session: Session, event_type: str, tenant_id, correlation_id: str, payload: dict, idempotency_key: str | None = None) -> None:
    session.add(OutboxEvent(event_type=event_type, tenant_id=tenant_id, correlation_id=correlation_id, idempotency_key=idempotency_key, payload=payload))


def audit(session: Session, tenant_id, actor: str, action: str, aggregate_type: str, aggregate_id: str,
          safe_before: dict | None = None, safe_after: dict | None = None, reason: str | None = None,
          correlation_id: str | None = None, source: str = "api", outcome: str = "success") -> None:
    session.add(AuditLog(
        tenant_id=tenant_id, actor=actor or "system", action=action, aggregate_type=aggregate_type,
        aggregate_id=str(aggregate_id), safe_before=safe_before or {}, safe_after=safe_after or {},
        reason=reason, source=source, correlation_id=correlation_id or correlation(None), outcome=outcome,
    ))


def timeline(session: Session, tenant_id, category: str, safe_summary: str, actor: str | None = None,
             customer_id=None, lead_id=None, external_type: str | None = None, external_id: str | None = None,
             correlation_id: str | None = None) -> None:
    session.add(TimelineEntry(
        tenant_id=tenant_id, customer_id=customer_id, lead_id=lead_id, category=category,
        safe_summary=safe_summary, actor=actor, external_type=external_type, external_id=external_id,
        correlation_id=correlation_id or correlation(None),
    ))


def record_audit(session: Session, tenant_id, actor: str, action: str, aggregate_type: str, aggregate_id: str, safe_after: dict | None = None, reason: str | None = None) -> str:
    request_id = correlation(None)
    audit(session, tenant_id, actor, action, aggregate_type, aggregate_id, safe_after=safe_after, reason=reason, correlation_id=request_id)
    return request_id
