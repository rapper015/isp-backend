"""Follow-up management and due/overdue scheduling."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import FollowUp
from .audit_service import audit, correlation, outbox, timeline


def due_followups(session: Session, tenant_id, now: datetime | None = None) -> list[FollowUp]:
    now = now or datetime.now(timezone.utc)
    return list(session.scalars(select(FollowUp).where(FollowUp.tenant_id == tenant_id, FollowUp.status == "PENDING", FollowUp.scheduled_at <= now).order_by(FollowUp.scheduled_at)))


def overdue_followups(session: Session, tenant_id, grace_minutes: int = 15, now: datetime | None = None) -> list[FollowUp]:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=grace_minutes)
    return list(session.scalars(select(FollowUp).where(FollowUp.tenant_id == tenant_id, FollowUp.status == "PENDING", FollowUp.scheduled_at < cutoff).order_by(FollowUp.scheduled_at)))


def mark_due(session: Session, tenant_id, followup_id, actor: str | None = None) -> FollowUp:
    """Mark a follow-up as due and publish the due event (used by a worker)."""
    followup = session.scalar(select(FollowUp).where(FollowUp.id == followup_id, FollowUp.tenant_id == tenant_id))
    if followup is None:
        raise ValueError("follow-up not found")
    if followup.reminder_sent_at is None:
        followup.reminder_sent_at = datetime.now(timezone.utc)
        request_id = correlation(None)
        outbox(session, "crm.followup.due.v1", tenant_id, request_id, {"followup_id": str(followup.id), "lead_id": str(followup.lead_id) if followup.lead_id else None, "customer_id": str(followup.customer_id) if followup.customer_id else None})
        audit(session, tenant_id, actor or "system", "crm.followup.due", "followup", followup.id, correlation_id=request_id)
        session.flush()
    return followup


def mark_overdue(session: Session, tenant_id, followup_id) -> FollowUp:
    followup = session.scalar(select(FollowUp).where(FollowUp.id == followup_id, FollowUp.tenant_id == tenant_id))
    if followup is None:
        raise ValueError("follow-up not found")
    if followup.status == "PENDING":
        followup.status = "MISSED"
        request_id = correlation(None)
        outbox(session, "crm.followup.overdue.v1", tenant_id, request_id, {"followup_id": str(followup.id)})
        audit(session, tenant_id, "system", "crm.followup.overdue", "followup", followup.id, correlation_id=request_id)
        session.flush()
    return followup
