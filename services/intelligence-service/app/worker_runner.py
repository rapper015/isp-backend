"""Worker runner: run the intelligence pipelines on a poll loop."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from . import tasks
from .database import SessionLocal
from .models import Base

logger = logging.getLogger("intelligence.worker")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def run_once(interval_seconds: int = 300, *, create_all: bool = True) -> dict:
    if create_all:
        Base.metadata.create_all(bind=__import__("app.database", fromlist=["engine"]).engine)
    started = _now()
    counts = {"features": 0, "quality": 0, "expired_risk": 0, "expired_recommendations": 0,
              "stale_features": 0, "drift": 0, "outbox": 0, "stale_intents": 0}
    with SessionLocal() as session:
        try:
            counts["features"] = tasks.run_compute_features(session, None)
            counts["quality"] = tasks.run_quality_checks(session, None)
            counts["expired_risk"] = tasks.run_expire_risk_records(session, None)
            counts["expired_recommendations"] = tasks.run_expire_recommendations(session, None)
            counts["stale_features"] = tasks.run_mark_stale_features(session, None)
            counts["drift"] = tasks.run_detect_drift(session, None)
            counts["outbox"] = tasks.run_flush_outbox(session)
            counts["stale_intents"] = tasks.run_close_stale_intents(session, None)
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
            logger.info("intelligence worker cycle: %s", counts)
        except Exception:  # noqa: BLE001
            logger.exception("intelligence worker cycle failed")
        elapsed = time.monotonic() - started
        time.sleep(max(1.0, interval_seconds - elapsed))
