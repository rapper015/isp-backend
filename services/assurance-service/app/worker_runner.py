"""Worker runner: iterate tenants, run scheduled tasks."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from . import tasks
from .database import SessionLocal
from .models import Base

logger = logging.getLogger("assurance.worker")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def run_once(interval_seconds: int = 300, *, create_all: bool = True) -> dict:
    if create_all:
        Base.metadata.create_all(bind=__import__("app.database", fromlist=["engine"]).engine)
    started = _now()
    counts = {"slo_windows": 0, "alerts_expired": 0, "silences_closed": 0,
              "maintenance_activated": 0, "maintenance_completed": 0, "outbox_flushed": 0}
    with SessionLocal() as session:
        try:
            counts["slo_windows"] = tasks.run_compute_slo_windows(session, None)
            counts["alerts_expired"] = tasks.run_expire_alerts(session, None)
            counts["silences_closed"] = tasks.run_close_silences(session)
            counts["maintenance_activated"] = tasks.run_activate_maintenance(session, None)
            counts["maintenance_completed"] = tasks.run_complete_maintenance(session, None)
            counts["outbox_flushed"] = tasks.run_flush_outbox(session)
            session.commit()
        except Exception:  # noqa: BLE001
            session.rollback()
            logger.exception("worker run failed")
    counts["duration_ms"] = int((_now() - started).total_seconds() * 1000)
    return counts


def run_loop(interval_seconds: int = 300):
    while True:
        started = time.monotonic()
        try:
            counts = run_once(interval_seconds=interval_seconds)
            logger.info("assurance worker cycle: %s", counts)
        except Exception:  # noqa: BLE001
            logger.exception("assurance worker cycle failed")
        elapsed = time.monotonic() - started
        time.sleep(max(1.0, interval_seconds - elapsed))
