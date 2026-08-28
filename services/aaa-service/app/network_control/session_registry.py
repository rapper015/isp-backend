"""Session registry helpers: immutable timeline projection and stale/orphan
detection built on top of the existing ActiveSession registry.

Accounting records remain immutable; the current-session view is a projection.
Stale detection never marks a session stopped solely on one delayed interim."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ActiveSession, SessionTimeline


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(value: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes for timezone-aware columns; assume UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def record_timeline(session: Session, tenant_id, session_row: ActiveSession, event_type: str, payload: dict, correlation_id: str | None = None) -> None:
    session.add(
        SessionTimeline(
            tenant_id=tenant_id,
            session_id=session_row.id,
            event_type=event_type,
            payload=payload,
            correlation_id=correlation_id,
        )
    )


def timeline(session: Session, tenant_id, session_id) -> list[SessionTimeline]:
    return list(
        session.scalars(
            select(SessionTimeline)
            .where(SessionTimeline.tenant_id == tenant_id, SessionTimeline.session_id == session_id)
            .order_by(SessionTimeline.created_at)
        )
    )


def classify_stale(session: Session, tenant_id, interim_threshold_seconds: int = 600, now: datetime | None = None) -> list[ActiveSession]:
    """Mark STARTING/ACTIVE sessions as STALE when they missed their interim
    update window. A single delayed interim does not stop the session."""
    now = now or _now()
    threshold = now - timedelta(seconds=interim_threshold_seconds)
    candidates = list(
        session.scalars(
            select(ActiveSession).where(
                ActiveSession.tenant_id == tenant_id,
                ActiveSession.status.in_(["STARTING", "ACTIVE"]),
            )
        )
    )
    stale: list[ActiveSession] = []
    for item in candidates:
        reference = _as_aware(item.last_interim_at) or _as_aware(item.started_at)
        if reference is None or reference < threshold:
            item.status = "STALE"
            stale.append(item)
    return stale


def detect_orphans(session: Session, tenant_id, orphan_after_seconds: int = 3600, now: datetime | None = None) -> list[ActiveSession]:
    """Sessions stale for too long (no stop received) become ORPHANED."""
    now = now or _now()
    threshold = now - timedelta(seconds=orphan_after_seconds)
    candidates = list(
        session.scalars(
            select(ActiveSession).where(
                ActiveSession.tenant_id == tenant_id,
                ActiveSession.status == "STALE",
            )
        )
    )
    orphaned: list[ActiveSession] = []
    for item in candidates:
        reference = _as_aware(item.last_interim_at) or _as_aware(item.started_at)
        if reference is None or reference < threshold:
            item.status = "ORPHANED"
            orphaned.append(item)
    return orphaned
