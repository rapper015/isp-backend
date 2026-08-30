"""Churn prediction + retention candidate tracking.

The churn score never issues discounts — it records a recommended action that
flows through CRM workflows."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.churn import retention_action, risk_band, top_drivers
from ..domain.statistics import weighted_logit
from ..domain.exceptions import NotFoundError
from ..models import ChurnScore, MlModel, RetentionCandidate
from .audit_service import outbox
from .feature_service import apply_missing_defaults, compute_features


def _now() -> datetime:
    return datetime.now(timezone.utc)


def score_customer(session: Session, *, tenant_id, customer_ref: str, service_ref: str | None = None,
                   horizon_days: int = 30, model_code: str = "churn_baseline_30d",
                   as_of: datetime | None = None, correlation_id: str | None = None) -> ChurnScore:
    model = session.execute(select(MlModel).where(MlModel.model_code == model_code,
                                                  MlModel.state == "PRODUCTION")
                            .order_by(MlModel.version.desc()).limit(1)).scalars().first()
    if model is None:
        raise NotFoundError(f"no production model for {model_code}")
    as_of = as_of or _now()
    values = compute_features(session, tenant_id=tenant_id, entity_type="customer",
                              entity_ref=customer_ref, as_of=as_of,
                              feature_names=model.feature_names)
    # Missing values use model defaults.
    definitions = []
    from ..models import FeatureDefinition
    if model.feature_names:
        definitions = list(session.scalars(select(FeatureDefinition).where(
            FeatureDefinition.name.in_(model.feature_names))))
    vector = apply_missing_defaults(values, definitions)
    weights = model.parameters.get("weights", {})
    intercept = model.parameters.get("intercept", 0.0)
    score = weighted_logit(vector, weights, intercept)
    drivers = {name: weights.get(name, 0.0) * float(vector.get(name, 0.0) or 0.0)
               for name in model.feature_names}
    band = risk_band(score)
    row = ChurnScore(tenant_id=tenant_id, customer_ref=customer_ref, service_ref=service_ref,
                     horizon_days=horizon_days, score=round(score, 4), risk_band=band,
                     confidence=round(min(1.0, 0.5 + score), 4),
                     top_drivers=top_drivers(drivers),
                     feature_timestamp=as_of, model_code=model.model_code,
                     model_version=model.version,
                     expiry_at=as_of + timedelta(days=max(1, horizon_days // 30)),
                     state="ACTIVE", recommended_action=retention_action(band))
    session.add(row)
    session.flush()
    outbox(session, "ai.churn_risk_updated.v1", tenant_id, correlation_id,
           {"churn_score_id": str(row.id), "customer_ref": customer_ref,
            "risk_band": band, "score": row.score, "horizon_days": horizon_days},
           idempotency_key=f"churn:{row.id}")
    return row


def create_retention_candidate(session: Session, churn_score_id: uuid.UUID, *,
                               recommended_action: str | None = None) -> RetentionCandidate:
    score = session.get(ChurnScore, churn_score_id)
    if score is None:
        raise NotFoundError("churn score not found")
    row = RetentionCandidate(tenant_id=score.tenant_id, churn_score_id=score.id,
                             customer_ref=score.customer_ref,
                             recommended_action=recommended_action or score.recommended_action or "MONITOR",
                             offer_presented=False, consent_granted=False)
    session.add(row)
    session.flush()
    return row


def track_offer(session: Session, candidate_id: uuid.UUID, *, presented: bool, consent: bool,
                accepted: bool | None = None, outcome: str | None = None,
                experiment_id: str | None = None) -> RetentionCandidate:
    row = session.get(RetentionCandidate, candidate_id)
    if row is None:
        raise NotFoundError("retention candidate not found")
    row.offer_presented = presented
    row.consent_granted = consent
    if accepted is not None:
        row.offer_accepted = accepted
    if outcome:
        row.outcome = outcome
    if experiment_id:
        row.experiment_id = experiment_id
    return row


def expire_scores(session: Session, tenant_id, *, now: datetime | None = None) -> int:
    now = now or _now()
    rows = list(session.scalars(select(ChurnScore).where(
        ChurnScore.tenant_id == tenant_id, ChurnScore.state == "ACTIVE",
        ChurnScore.expiry_at < now)))
    for row in rows:
        row.state = "EXPIRED"
    session.flush()
    return len(rows)
