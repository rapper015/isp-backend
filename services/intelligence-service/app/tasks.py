"""Background tasks: feature refresh, drift checks, expiry, quality, outbox."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .events import unprocessed_events
from .models import (AnalyticalRecord, ChurnScore, FeatureDefinition, FailurePrediction,
                     FraudSignal, MlModel, Recommendation, RemediationIntent, TrainingRun)
from .services import churn_service, feature_service, fraud_service, ml_service, quality_service

logger = logging.getLogger("intelligence.tasks")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def run_compute_features(session: Session, tenant_id, *, batch: int = 200) -> int:
    """Recompute + store features for recently active analytical entities."""
    counts = 0
    active = list(session.scalars(select(AnalyticalRecord.entity_type, AnalyticalRecord.entity_ref)
                                  .where(AnalyticalRecord.tenant_id == tenant_id)
                                  .order_by(AnalyticalRecord.event_time.desc()).limit(batch).distinct()))
    for entity_type, entity_ref in active:
        try:
            values = feature_service.compute_features(
                session, tenant_id=tenant_id, entity_type=entity_type, entity_ref=entity_ref)
            if values:
                feature_service.store_feature_values(session, tenant_id=tenant_id,
                                                     entity_type=entity_type,
                                                     entity_ref=entity_ref, values=values)
                counts += 1
        except Exception:  # noqa: BLE001
            logger.exception("feature computation failed %s %s", entity_type, entity_ref)
    session.flush()
    return counts


def run_quality_checks(session: Session, tenant_id) -> int:
    contracts = list(session.scalars(select(AnalyticalRecord.contract).distinct()))
    count = 0
    for contract in contracts:
        try:
            quality_service.measure_quality(session, tenant_id, contract)
            count += 1
        except Exception:  # noqa: BLE001
            logger.exception("quality check failed %s", contract)
    session.flush()
    return count


def run_expire_risk_records(session: Session, tenant_id, *, now: datetime | None = None) -> int:
    now = now or _now()
    count = 0
    churn = list(session.scalars(select(ChurnScore).where(
        ChurnScore.tenant_id == tenant_id, ChurnScore.state == "ACTIVE",
        ChurnScore.expiry_at < now)))
    for row in churn:
        row.state = "EXPIRED"
        count += 1
    failure = list(session.scalars(select(FailurePrediction).where(
        FailurePrediction.tenant_id == tenant_id, FailurePrediction.state == "ACTIVE",
        FailurePrediction.expiry_at < now)))
    for row in failure:
        row.state = "EXPIRED"
        count += 1
    session.flush()
    return count


def run_expire_recommendations(session: Session, tenant_id, *, now: datetime | None = None) -> int:
    now = now or _now()
    rows = list(session.scalars(select(Recommendation).where(
        Recommendation.tenant_id == tenant_id, Recommendation.state == "OPEN",
        Recommendation.expires_at < now)))
    for row in rows:
        row.state = "EXPIRED"
    session.flush()
    return len(rows)


def run_mark_stale_features(session: Session, tenant_id, *, max_age_seconds: int = 86400) -> int:
    return feature_service.mark_stale_features(session, tenant_id, max_age_seconds=max_age_seconds)


def run_detect_drift(session: Session, tenant_id, *, threshold: float = 0.2) -> int:
    """Compare recent prediction distribution to the model's evaluation mean."""
    count = 0
    models = list(session.scalars(select(MlModel).where(
        MlModel.tenant_id == tenant_id,
        MlModel.state.in_(("SHADOW", "CANARY", "PRODUCTION")))))
    for model in models:
        expected = model.evaluation_metrics.get("precision", 0.5) or 0.5
        observed = _recent_mean(session, model.id)
        if observed is None:
            continue
        ml_service.detect_drift(session, model.id, expected_mean=expected, observed_mean=observed,
                                threshold=threshold)
        count += 1
    session.flush()
    return count


def _recent_mean(session: Session, model_id) -> float | None:
    from ..models import ModelMonitor
    since = _now() - timedelta(hours=24)
    row = session.execute(select(func.avg(ModelMonitor.value)).where(
        ModelMonitor.model_id == model_id,
        ModelMonitor.metric_type.in_(("prediction_distribution", "prediction_drift")),
        ModelMonitor.window_end >= since)).scalar()
    return float(row) if row is not None else None


def run_flush_outbox(session: Session) -> int:
    events = unprocessed_events(session)
    for event in events:
        event.published_at = _now()
    session.flush()
    return len(events)


def run_close_stale_intents(session: Session, tenant_id, *, max_age_hours: int = 24) -> int:
    cutoff = _now() - timedelta(hours=max_age_hours)
    rows = list(session.scalars(select(RemediationIntent).where(
        RemediationIntent.tenant_id == tenant_id,
        RemediationIntent.state == "STARTED", RemediationIntent.executed_at < cutoff)))
    for row in rows:
        row.state = "FAILED"
    session.flush()
    return len(rows)
