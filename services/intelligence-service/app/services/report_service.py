"""Role-specific insights + platform aggregates (require PLATFORM scope)."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (ChurnScore, FailurePrediction, FraudCase, FraudSignal, MlModel,
                      ModelMonitor, Recommendation, RemediationIntent, TrainingRun)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def executive_dashboard(session: Session, *, days: int = 7) -> dict:
    since = _now() - timedelta(days=days)
    return {
        "fraud_cases": session.scalar(select(func.count(FraudCase.id)).where(FraudCase.opened_at >= since)) or 0,
        "high_risk_churn": session.scalar(select(func.count(ChurnScore.id)).where(
            ChurnScore.risk_band.in_(("HIGH", "CRITICAL")))) or 0,
        "failure_predictions_high": session.scalar(select(func.count(FailurePrediction.id)).where(
            FailurePrediction.degradation_risk == "HIGH")) or 0,
        "open_recommendations": session.scalar(select(func.count(Recommendation.id)).where(
            Recommendation.state == "OPEN")) or 0,
        "active_models": session.scalar(select(func.count(MlModel.id)).where(
            MlModel.state.in_(("SHADOW", "CANARY", "PRODUCTION")))) or 0,
        "remediation_completed": session.scalar(select(func.count(RemediationIntent.id)).where(
            RemediationIntent.state == "COMPLETED", RemediationIntent.requested_at >= since)) or 0,
        "is_platform_aggregate": True,
    }


def tenant_insights(session: Session, tenant_id, *, days: int = 7) -> dict:
    since = _now() - timedelta(days=days)
    return {
        "tenant_id": str(tenant_id),
        "fraud_signals": session.scalar(select(func.count(FraudSignal.id)).where(
            FraudSignal.tenant_id == tenant_id, FraudSignal.detection_time >= since)) or 0,
        "fraud_cases_open": session.scalar(select(func.count(FraudCase.id)).where(
            FraudCase.tenant_id == tenant_id, FraudCase.state.in_(("OPEN", "IN_REVIEW")))) or 0,
        "churn_high_risk": session.scalar(select(func.count(ChurnScore.id)).where(
            ChurnScore.tenant_id == tenant_id, ChurnScore.risk_band.in_(("HIGH", "CRITICAL")))) or 0,
        "failure_predictions": session.scalar(select(func.count(FailurePrediction.id)).where(
            FailurePrediction.tenant_id == tenant_id)) or 0,
        "open_recommendations": session.scalar(select(func.count(Recommendation.id)).where(
            Recommendation.tenant_id == tenant_id, Recommendation.state == "OPEN")) or 0,
        "remediation_pending": session.scalar(select(func.count(RemediationIntent.id)).where(
            RemediationIntent.tenant_id == tenant_id, RemediationIntent.state == "PENDING")) or 0,
    }


def model_health(session: Session, tenant_id) -> dict:
    rows = list(session.scalars(select(ModelMonitor).where(
        ModelMonitor.tenant_id == tenant_id).order_by(ModelMonitor.window_end.desc()).limit(50)))
    return {"monitors": [{"model_id": str(m.model_id), "metric_type": m.metric_type,
                          "value": m.value, "alert": m.alert,
                          "window_end": m.window_end.isoformat()} for m in rows],
            "alerted": any(m.alert for m in rows)}


def training_history(session: Session, tenant_id, *, limit: int = 50) -> list[dict]:
    rows = list(session.scalars(select(TrainingRun).where(
        TrainingRun.tenant_id == tenant_id).order_by(TrainingRun.started_at.desc()).limit(limit)))
    return [{"run_id": r.run_id, "model_code": r.model_code, "state": r.state,
             "algorithm": r.algorithm, "metrics": r.metrics,
             "started_at": r.started_at.isoformat() if r.started_at else None} for r in rows]
