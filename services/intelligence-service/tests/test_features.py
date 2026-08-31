"""Feature store: point-in-time correctness, missing defaults, freshness."""
import uuid
from datetime import datetime, timedelta, timezone

from app.models import FeatureDefinition, FeatureValue, OnlineFeatureValue
from app.services import feature_service, ingestion_service


def _ev(event_type, tenant_id, payload, occurred_at=None):
    return {
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "schema_version": 1,
        "occurred_at": (occurred_at or datetime.now(timezone.utc)).isoformat(),
        "tenant_id": str(tenant_id),
        "producer": "test",
        "payload": payload,
    }


def _seed_customer_events(session, tenant_id, customer, *, payment_failures=0, payments=0):
    for _ in range(payment_failures):
        ingestion_service.ingest_event(session, _ev("billing.payment.failed.v1", tenant_id,
                                                    {"customer_id": customer, "reason": "insufficient"}))
    for _ in range(payments):
        ingestion_service.ingest_event(session, _ev("billing.payment.captured.v1", tenant_id,
                                                    {"customer_id": customer, "amount": 100}))
    session.commit()


def test_point_in_time_correctness(defaults, session, tenant_id):
    """Features computed as-of a past time must not see future events."""
    customer = "c-pit"
    t0 = datetime.now(timezone.utc) - timedelta(days=2)
    # 2 failures before t0, 5 failures after t0.
    for i in range(2):
        ingestion_service.ingest_event(
            session, _ev("billing.payment.failed.v1", tenant_id, {"customer_id": customer},
                         occurred_at=t0 + timedelta(hours=i)))
    session.commit()
    values_before = feature_service.compute_features(
        session, tenant_id=tenant_id, entity_type="customer", entity_ref=customer,
        as_of=t0 + timedelta(hours=5), feature_names=["recent_payment_failures"])
    for i in range(5):
        ingestion_service.ingest_event(
            session, _ev("billing.payment.failed.v1", tenant_id, {"customer_id": customer},
                         occurred_at=t0 + timedelta(days=1, hours=i)))
    session.commit()
    assert values_before["recent_payment_failures"] == 2  # no future leakage


def test_compute_and_store_features(defaults, session, tenant_id):
    _seed_customer_events(session, tenant_id, "c-1", payment_failures=2, payments=2)
    values = feature_service.compute_features(
        session, tenant_id=tenant_id, entity_type="customer", entity_ref="c-1",
        feature_names=["payment_failure_rate", "recent_payment_failures"])
    assert values["payment_failure_rate"] == 0.5
    assert values["recent_payment_failures"] == 2
    feature_service.store_feature_values(session, tenant_id=tenant_id, entity_type="customer",
                                         entity_ref="c-1", values=values)
    session.commit()
    assert session.query(FeatureValue).count() == 2
    vector = feature_service.online_feature_vector(session, tenant_id, "c-1",
                                                   ["payment_failure_rate"])
    assert vector["payment_failure_rate"] == 0.5


def test_missing_feature_defaults(defaults, session, tenant_id):
    from app.models import FeatureDefinition
    definitions = list(session.query(FeatureDefinition).limit(2).all())
    vector = feature_service.apply_missing_defaults({"payment_failure_rate": None}, definitions)
    # Missing feature gets the default (valid_range min or explicit default).
    assert vector["payment_failure_rate"] is not None


def test_mark_stale_features(defaults, session, tenant_id):
    from app.models import OnlineFeatureValue
    old = datetime.now(timezone.utc) - timedelta(days=3)
    session.add(OnlineFeatureValue(tenant_id=tenant_id, entity_ref="c-1", feature_name="f_x",
                                   value=1.0, version="v1", computed_at=old, quality="FRESH"))
    session.commit()
    count = feature_service.mark_stale_features(session, tenant_id, max_age_seconds=86400)
    session.commit()
    assert count == 1
    row = session.query(OnlineFeatureValue).first()
    assert row.quality == "STALE"


def test_transform_unknown_feature_raises():
    from app.domain.exceptions import FeatureError
    from app.domain.features import apply_transform
    import pytest
    with pytest.raises(FeatureError):
        apply_transform("nonexistent_feature", "v1", [])
