"""Data foundation: contract validation, dedup, quarantine, late events,
backfill/replay."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.events import consume_once, envelope, outbox, unprocessed_events
from app.models import AnalyticalRecord, RawEvent
from app.services import ingestion_service


def _ev(event_type, tenant_id, payload=None, event_id=None, occurred_at=None):
    return {
        "event_id": event_id or str(uuid.uuid4()),
        "event_type": event_type,
        "schema_version": 1,
        "occurred_at": (occurred_at or datetime.now(timezone.utc)).isoformat(),
        "tenant_id": str(tenant_id),
        "correlation_id": "corr-1",
        "causation_id": None,
        "idempotency_key": str(uuid.uuid4()),
        "producer": "test",
        "trace_context": {},
        "payload": payload or {},
    }


def test_contracts_seeded(defaults, session):
    from app.models import DataContract
    names = {c.event_name for c in session.query(DataContract).all()}
    assert "billing.payment.failed.v1" in names
    assert "assurance.incident_created.v1" in names


def test_ingest_valid_event_creates_raw_and_analytical(defaults, session, tenant_id):
    raw = ingestion_service.ingest_event(session, _ev("billing.payment.captured.v1", tenant_id,
                                                      {"customer_id": "c-1", "amount": 100}))
    session.commit()
    assert raw.state == "VALID"
    assert session.query(RawEvent).count() == 1
    records = session.query(AnalyticalRecord).all()
    assert len(records) == 1
    assert records[0].entity_type == "customer"
    assert records[0].entity_ref == "c-1"


def test_duplicate_event_idempotent(defaults, session, tenant_id):
    event_id = str(uuid.uuid4())
    ingestion_service.ingest_event(session, _ev("billing.payment.captured.v1", tenant_id,
                                                {"customer_id": "c-1"}, event_id=event_id))
    session.commit()
    raw2 = ingestion_service.ingest_event(session, _ev("billing.payment.captured.v1", tenant_id,
                                                       {"customer_id": "c-1"}, event_id=event_id))
    session.commit()
    assert raw2 is None  # duplicate suppressed
    assert session.query(RawEvent).count() == 1
    assert session.query(AnalyticalRecord).count() == 1


def test_missing_required_field_quarantined(defaults, session, tenant_id):
    raw = ingestion_service.ingest_event(session, _ev("billing.payment.captured.v1", tenant_id, {}))
    session.commit()
    assert raw.state == "QUARANTINED"
    assert session.query(AnalyticalRecord).count() == 0


def test_unknown_contract_quarantined(defaults, session, tenant_id):
    raw = ingestion_service.ingest_event(session, _ev("unknown.type.v1", tenant_id, {}))
    session.commit()
    assert raw.state == "QUARANTINED"


def test_pii_stripped_from_analytical_record(defaults, session, tenant_id):
    # crm.customer.created.v1 marks `name` as PII.
    raw = ingestion_service.ingest_event(session, _ev("crm.customer.created.v1", tenant_id,
                                                      {"customer_id": "c-1", "name": "Alice"}))
    session.commit()
    record = session.query(AnalyticalRecord).first()
    assert "name" not in record.normalized
    assert record.normalized["customer_id"] == "c-1"
    # Raw still retains the PII for reproducibility.
    assert raw.payload["name"] == "Alice"


def test_sensitive_contract_never_ingested(defaults, session, tenant_id):
    raw = ingestion_service.ingest_event(session, _ev("card.provider.charge.v1", tenant_id,
                                                      {"card": "4111"}))
    session.commit()
    assert raw.state == "QUARANTINED"


def test_late_event_handling(defaults, session, tenant_id):
    late = datetime.now(timezone.utc) - timedelta(days=10)
    raw = ingestion_service.ingest_event(session, _ev("crm.customer.created.v1", tenant_id,
                                                      {"customer_id": "late-1"}, occurred_at=late))
    session.commit()
    assert raw.state == "VALID"
    record = session.query(AnalyticalRecord).first()
    assert record.event_time.date() == late.date()  # event-time preserved


def test_replay_backfill(defaults, session, tenant_id):
    # Seed raw events but delete analytical records, then replay.
    for _ in range(3):
        ingestion_service.ingest_event(session, _ev("billing.payment.captured.v1", tenant_id,
                                                    {"customer_id": "c-1"}))
    session.commit()
    session.query(AnalyticalRecord).delete()
    session.commit()
    count = ingestion_service.replay_raw_events(session, "billing.payment.captured.v1")
    session.commit()
    assert count == 3
    assert session.query(AnalyticalRecord).count() == 3
    # Replay again is idempotent.
    count2 = ingestion_service.replay_raw_events(session, "billing.payment.captured.v1")
    session.commit()
    assert count2 == 0
    assert session.query(AnalyticalRecord).count() == 3


def test_consume_once_idempotent(session):
    event_id = str(uuid.uuid4())
    assert consume_once(session, event_id, "intelligence:test") is True
    session.flush()
    assert consume_once(session, event_id, "intelligence:test") is False


def test_outbox_unprocessed(session):
    outbox(session, "ai.prediction_created.v1", uuid.uuid4(), None, {"x": 1})
    session.commit()
    pending = unprocessed_events(session)
    assert len(pending) == 1
    assert pending[0].event_type == "ai.prediction_created.v1"


def test_quality_measurement(defaults, session, tenant_id):
    for _ in range(10):
        ingestion_service.ingest_event(session, _ev("billing.payment.captured.v1", tenant_id,
                                                    {"customer_id": "c-1"}))
    session.commit()
    from app.services.quality_service import measure_quality
    quality = measure_quality(session, tenant_id, "billing.payment.captured.v1")
    session.commit()
    assert quality["overall"] == "PASS"
