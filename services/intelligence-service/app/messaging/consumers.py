"""Idempotent consumers mapping domain events into the intelligence pipeline:
raw events -> analytical records -> (worker) features."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..events import canonical_event_type, consume_once
from ..services import ingestion_service

logger = logging.getLogger("intelligence.consumers")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def handle(session: Session, envelope: dict) -> None:
    event_type = envelope.get("event_type", "")
    event_id = envelope.get("event_id")
    if not event_id:
        logger.warning("dropping event without id: %s", event_type)
        return
    try:
        canonical = canonical_event_type(event_type)
    except ValueError:
        logger.info("ignoring unconsumed event type %s", event_type)
        return
    consumer = f"intelligence:{canonical}"
    if not consume_once(session, str(event_id), consumer):
        logger.info("duplicate event %s (already consumed)", event_id)
        return
    try:
        raw = ingestion_service.ingest_event(session, envelope, source="event")
        if raw is None:
            logger.info("raw event %s already present (idempotent)", event_id)
        elif raw.state == "QUARANTINED":
            logger.warning("quarantined event %s contract=%s", event_id, canonical)
        session.flush()
    except Exception:  # noqa: BLE001
        logger.exception("ingestion failed for %s", event_id)
        raise
