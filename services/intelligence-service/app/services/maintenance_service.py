"""Predictive maintenance + capacity forecasting.

Predictions create maintenance warnings / remediation intents only — they never
reboot devices, replace inventory or change configuration without approval."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.maintenance import (capacity_risk, degradation_band,
                                  maintenance_recommendation, weighted_failure_score)
from ..domain.statistics import confidence_interval, linear_forecast
from ..domain.exceptions import NotFoundError
from ..models import CapacityForecast, FailurePrediction, MlModel
from .audit_service import outbox
from .feature_service import apply_missing_defaults, compute_features


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _production_model(session: Session, model_code: str) -> MlModel:
    model = session.execute(select(MlModel).where(MlModel.model_code == model_code,
                                                  MlModel.state == "PRODUCTION")
                            .order_by(MlModel.version.desc()).limit(1)).scalars().first()
    if model is None:
        raise NotFoundError(f"no production model for {model_code}")
    return model


def predict_failure(session: Session, *, tenant_id, asset_type: str, asset_ref: str,
                    model_code: str = "maintenance_baseline", horizon_days: int = 14,
                    as_of: datetime | None = None, correlation_id: str | None = None) -> FailurePrediction:
    model = _production_model(session, model_code)
    as_of = as_of or _now()
    values = compute_features(session, tenant_id=tenant_id, entity_type=_entity_type(asset_type),
                              entity_ref=asset_ref, as_of=as_of, feature_names=model.feature_names)
    from ..models import FeatureDefinition
    definitions = list(session.scalars(select(FeatureDefinition).where(
        FeatureDefinition.name.in_(model.feature_names)))) if model.feature_names else []
    vector = apply_missing_defaults(values, definitions)
    probability = weighted_failure_score(vector, model.parameters.get("weights", {}))
    row = FailurePrediction(
        tenant_id=tenant_id, asset_type=asset_type, asset_ref=asset_ref,
        failure_probability=probability, horizon_days=horizon_days,
        degradation_risk=degradation_band(probability),
        confidence=round(min(1.0, 0.5 + probability), 4),
        evidence=[{"feature": k, "value": v} for k, v in vector.items()],
        recommendation_type=maintenance_recommendation(probability),
        model_code=model.model_code, model_version=model.version,
        computed_at=as_of, expiry_at=as_of + timedelta(days=horizon_days), state="ACTIVE")
    session.add(row)
    session.flush()
    if probability >= 0.25:
        outbox(session, "ai.failure_risk_detected.v1", tenant_id, correlation_id,
               {"prediction_id": str(row.id), "asset_ref": asset_ref,
                "probability": probability, "recommendation_type": row.recommendation_type},
               idempotency_key=f"failure:{row.id}")
    return row


def forecast_capacity(session: Session, *, tenant_id, resource_type: str, resource_ref: str,
                      utilization_series: list[float], horizon_days: int = 30,
                      model_code: str | None = None, as_of: datetime | None = None) -> CapacityForecast:
    as_of = as_of or _now()
    points = linear_forecast(utilization_series, horizon_days)
    peak = max(points) if points else 0.0
    ci = confidence_interval(points)
    row = CapacityForecast(
        tenant_id=tenant_id, resource_type=resource_type, resource_ref=resource_ref,
        horizon_days=horizon_days,
        forecast={"points": points, "current_utilization": utilization_series[-1] if utilization_series else 0.0},
        confidence_interval=ci, risk=capacity_risk(utilization_series[-1] if utilization_series else 0.0, peak),
        model_code=model_code, computed_at=as_of, freshness=_now())
    session.add(row)
    session.flush()
    if row.risk in ("HIGH", "CRITICAL"):
        outbox(session, "ai.capacity_risk_detected.v1", tenant_id, None,
               {"forecast_id": str(row.id), "resource_ref": resource_ref,
                "risk": row.risk, "peak_utilization": peak},
               idempotency_key=f"capacity:{row.id}")
    return row


def _entity_type(asset_type: str) -> str:
    mapping = {"nas": "nas", "olt": "nas", "pon_port": "nas", "router": "nas",
               "ont": "cpe", "cpe": "cpe"}
    return mapping.get(asset_type, "cpe")
