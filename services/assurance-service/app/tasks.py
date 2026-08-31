"""Background tasks: SLO windows, alert expiry, silence cleanup, outbox flush."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .domain.slos import window_bounds
from .events import unprocessed_events
from .models import Alert, AlertSilence, MaintenanceWindow, SloDefinition
from .services import alert_service, maintenance_service, slo_service

logger = logging.getLogger("assurance.tasks")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def run_compute_slo_windows(session: Session, tenant_id, *, now: datetime | None = None) -> int:
    now = now or _now()
    count = 0
    for slo in session.scalars(select(SloDefinition).where(SloDefinition.state == "ACTIVE")):
        version = slo_service.latest_version(session, slo.id)
        start, end = window_bounds(now, window_type=version.window_type, window_seconds=version.window_seconds)
        try:
            slo_service.compute_window(session, tenant_id, slo.id, window_start=start, window_end=end)
            count += 1
        except Exception:  # noqa: BLE001
            logger.exception("SLO window computation failed for %s", slo.code)
    session.flush()
    return count


def run_expire_alerts(session: Session, tenant_id, *, max_age_seconds: int = 48 * 3600,
                      now: datetime | None = None) -> int:
    now = now or _now()
    cutoff = now - timedelta(seconds=max_age_seconds)
    stale = list(session.scalars(select(Alert).where(
        Alert.state.in_(("PENDING",)),
        Alert.first_observed < cutoff)))
    count = 0
    for alert in stale:
        try:
            alert_service.expire(session, alert.id)
            count += 1
        except Exception:  # noqa: BLE001
            logger.exception("alert expiry failed %s", alert.id)
    session.flush()
    return count


def run_close_silences(session: Session, tenant_id, *, now: datetime | None = None) -> int:
    now = now or _now()
    expired = list(session.scalars(select(AlertSilence).where(
        AlertSilence.state == "ACTIVE", AlertSilence.ends_at < now)))
    count = 0
    for silence in expired:
        silence.state = "EXPIRED"
        count += 1
    session.flush()
    return count


def run_activate_maintenance(session: Session, tenant_id, *, now: datetime | None = None) -> int:
    now = now or _now()
    due = list(session.scalars(select(MaintenanceWindow).where(
        MaintenanceWindow.state == "APPROVED",
        MaintenanceWindow.starts_at <= now)))
    count = 0
    for window in due:
        try:
            maintenance_service.activate(session, window.id)
            count += 1
        except Exception:  # noqa: BLE001
            logger.exception("maintenance activation failed %s", window.id)
    session.flush()
    return count


def run_complete_maintenance(session: Session, tenant_id, *, now: datetime | None = None) -> int:
    now = now or _now()
    due = list(session.scalars(select(MaintenanceWindow).where(
        MaintenanceWindow.state == "ACTIVE",
        MaintenanceWindow.ends_at <= now)))
    count = 0
    for window in due:
        try:
            maintenance_service.complete(session, window.id)
            count += 1
        except Exception:  # noqa: BLE001
            logger.exception("maintenance completion failed %s", window.id)
    session.flush()
    return count


def run_flush_outbox(session: Session) -> int:
    events = unprocessed_events(session)
    count = 0
    for event in events:
        event.published_at = _now()
        count += 1
    session.flush()
    return count
