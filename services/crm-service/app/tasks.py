"""Background tasks for the CRM service: follow-up reminders and overdue
handling. An external scheduler (cron/systemd/Kubernetes CronJob) invokes these."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from .events import publish_outbox
from .services.followup_service import due_followups, mark_due, mark_overdue, overdue_followups


def process_followups(session: Session, grace_minutes: int = 15) -> dict[str, int]:
    """Mark due follow-ups (publish crm.followup.due.v1) and overdue follow-ups
    (publish crm.followup.overdue.v1)."""
    due = 0
    overdue = 0
    for item in _pending_followups(session):
        if item.scheduled_at <= datetime.now(timezone.utc):
            try:
                mark_due(session, item.tenant_id, item.id)
                due += 1
            except ValueError:
                pass
        if item.scheduled_at < datetime.now(timezone.utc) - timedelta(minutes=grace_minutes):
            try:
                mark_overdue(session, item.tenant_id, item.id)
                overdue += 1
            except ValueError:
                pass
    session.commit()
    published = publish_outbox(session)
    return {"due": due, "overdue": overdue, "events_published": published}


def _pending_followups(session: Session):
    from sqlalchemy import select
    from .models import FollowUp
    return list(session.scalars(select(FollowUp).where(FollowUp.status == "PENDING")))
