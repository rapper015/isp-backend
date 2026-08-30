"""Intelligence Service — AI & Intelligence Layer (Milestone 10).

FastAPI application exposing the governed intelligence surface: data contracts,
ingestion + quality, datasets, features, MLOps lifecycle (training/registry/
deployment/monitoring), fraud, churn, predictive maintenance, capacity
forecasting, recommendations and safe remediation intents.

Safety boundary: the AI layer never mutates domain state directly. Every
operational change flows through a remediation intent (policy evaluation,
approval, kill switch, budget/cooldown/circuit breaker) to the authoritative
service."""
from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from . import schemas
from .cache import limited
from .context import get_context, require_tenant, reset_context, set_context
from .database import Base, SessionLocal, engine
from .domain.exceptions import (IntelligenceError, TenantContextRequiredError, TenantIsolationError)
from .messaging import consumers
from .models import (ChurnScore, DataContract, DatasetSnapshot, FeatureDefinition,  # noqa: F401
                     FraudCase, FraudRule, KillSwitch, MlModel, ModelDeployment, ModelMonitor,
                     Recommendation, RemediationIntent, RemediationPolicy, TrainingRun)
from .routing import enforce_scope, require_platform_aggregate
from .security import internal_service_auth, management_auth
from .services import (aiops_advanced_service, audit_service, catalog_service, churn_service,
                       feature_service, fraud_service, ingestion_service, maintenance_service,
                       ml_service, operations_service, quality_service, remediation_service,
                       report_service)

logger = logging.getLogger("intelligence")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid(value) -> uuid.UUID:
    return uuid.UUID(str(value))


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as session:
        catalog_service.ensure_defaults(session)
        session.commit()
    yield


app = FastAPI(title="Intelligence Service", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def db():
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _raise(exc) -> None:
    raise exc


def _actor(request: Request) -> str | None:
    principal = getattr(request.state, "intelligence_principal", None)
    return principal.get("subject") if principal else None


def _tid(body_tenant=None) -> uuid.UUID | None:
    ctx = get_context()
    if body_tenant is not None:
        return _uuid(body_tenant)
    return ctx.tenant_id if ctx else None


@app.exception_handler(IntelligenceError)
async def intelligence_error_handler(request: Request, exc: IntelligenceError):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message, "code": exc.code})


@app.exception_handler(TenantContextRequiredError)
async def tenant_required_handler(request: Request, exc: TenantContextRequiredError):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=422, content={"detail": exc.message, "code": "TENANT_CONTEXT_REQUIRED"})


@app.exception_handler(TenantIsolationError)
async def isolation_handler(request: Request, exc: TenantIsolationError):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=403, content={"detail": exc.message, "code": "TENANT_ISOLATION"})


@app.get("/health")
def health():
    return {"status": "ok", "service": "intelligence"}


@app.get("/status")
def status(db: Session = Depends(db)):
    try:
        db.execute(__import__("sqlalchemy", fromlist=["text"]).text("SELECT 1"))
        db_ready = True
    except Exception:  # noqa: BLE001
        db_ready = False
    return {"status": "ok" if db_ready else "degraded", "service": "intelligence", "db": db_ready,
            "timestamp": _now().isoformat()}


# =====================================================================
# Internal ingest (service-to-service)
# =====================================================================
@app.post("/internal/intelligence/v1/ingest/event", dependencies=[Depends(internal_service_auth)])
def ingest_event(envelope: dict, db: Session = Depends(db)):
    consumers.handle(db, envelope)
    db.commit()
    return {"ok": True, "event_id": envelope.get("event_id")}


# =====================================================================
# Data contracts & ingestion
# =====================================================================
@app.get("/api/intelligence/v1/contracts", dependencies=[Depends(management_auth)])
def list_contracts(db: Session = Depends(db)):
    return [{"id": str(c.id), "event_name": c.event_name, "version": c.version,
             "producer": c.producer, "state": c.state, "retention_days": c.retention_days,
             "required_fields": c.required_fields, "pii_fields": c.pii_fields}
            for c in db.query(DataContract).order_by(DataContract.event_name).all()]


@app.post("/api/intelligence/v1/contracts", dependencies=[Depends(management_auth)])
def create_contract(payload: schemas.ContractIn, db: Session = Depends(db)):
    row = DataContract(event_name=payload.event_name, version=payload.version,
                       schema_json=payload.contract_schema, required_fields=payload.required_fields,
                       optional_fields=payload.optional_fields, pii_fields=payload.pii_fields,
                       producer=payload.producer, owner=payload.owner,
                       retention_days=payload.retention_days, state="ACTIVE")
    db.add(row)
    db.commit()
    return {"ok": True, "id": str(row.id), "event_name": row.event_name}


@app.post("/api/intelligence/v1/ingest", dependencies=[Depends(management_auth)])
def ingest_payload(payload: schemas.IngestEventIn, db: Session = Depends(db)):
    envelope = {
        "event_id": payload.event_id or str(uuid.uuid4()),
        "event_type": payload.event_type,
        "schema_version": 1,
        "occurred_at": (payload.occurred_at or _now()).isoformat(),
        "tenant_id": payload.tenant_id,
        "correlation_id": payload.correlation_id,
        "causation_id": payload.causation_id,
        "idempotency_key": payload.idempotency_key,
        "producer": payload.producer or "intelligence-client",
        "trace_context": {},
        "payload": payload.payload,
    }
    raw = ingestion_service.ingest_event(db, envelope, source="api")
    db.commit()
    if raw is None:
        return {"ok": True, "duplicate": True}
    return {"ok": True, "raw_event_id": str(raw.id), "state": raw.state}


@app.get("/api/intelligence/v1/raw-events", dependencies=[Depends(management_auth)])
def list_raw_events(contract: Optional[str] = None, state: Optional[str] = None, limit: int = 100,
                    db: Session = Depends(db)):
    q = db.query(__import__("app.models", fromlist=["RawEvent"]).RawEvent)
    if contract:
        q = q.filter(__import__("app.models", fromlist=["RawEvent"]).RawEvent.contract == contract)
    if state:
        q = q.filter(__import__("app.models", fromlist=["RawEvent"]).RawEvent.state == state)
    rows = q.order_by(__import__("app.models", fromlist=["RawEvent"]).RawEvent.event_time.desc()).limit(limit).all()
    return [{"id": str(r.id), "event_id": r.event_id, "contract": r.contract, "state": r.state,
             "tenant_id": str(r.tenant_id) if r.tenant_id else None,
             "event_time": r.event_time.isoformat(), "processing_time": r.processing_time.isoformat()}
            for r in rows]


@app.get("/api/intelligence/v1/quality", dependencies=[Depends(management_auth)])
def list_quality(contract: Optional[str] = None, db: Session = Depends(db)):
    from app.models import DataQualityCheck
    q = db.query(DataQualityCheck)
    if contract:
        q = q.filter(DataQualityCheck.contract == contract)
    rows = q.order_by(DataQualityCheck.measured_at.desc()).limit(200).all()
    return [{"id": str(r.id), "contract": r.contract, "check_type": r.check_type,
             "result": r.result, "detail": r.detail, "measured_at": r.measured_at.isoformat()} for r in rows]


@app.post("/api/intelligence/v1/quality/run", dependencies=[Depends(management_auth)])
def run_quality(contract: str, db: Session = Depends(db)):
    from app.models import DataQualityCheck
    result = quality_service.measure_quality(db, _tid(), contract)
    db.commit()
    return result


@app.post("/api/intelligence/v1/replay", dependencies=[Depends(management_auth)])
def replay(contract: Optional[str] = None, limit: int = 5000, db: Session = Depends(db)):
    count = ingestion_service.replay_raw_events(db, contract, limit=limit)
    db.commit()
    return {"ok": True, "replayed": count}


# =====================================================================
# Datasets
# =====================================================================
@app.get("/api/intelligence/v1/datasets", dependencies=[Depends(management_auth)])
def list_datasets(db: Session = Depends(db)):
    return [{"id": str(d.id), "code": d.code, "row_count": d.row_count, "state": d.state,
             "checksum": d.checksum} for d in db.query(DatasetSnapshot).all()]


@app.post("/api/intelligence/v1/datasets", dependencies=[Depends(management_auth)])
def create_dataset(payload: schemas.DatasetIn, request: Request, db: Session = Depends(db)):
    snap = quality_service.snapshot_dataset(db, tenant_id=_tid(), code=payload.code,
                                            name=payload.name, contracts=payload.contracts,
                                            criteria=payload.criteria, created_by=_actor(request))
    audit_service.audit(db, _tid(), _actor(request), "dataset.snapshot", resource_type="dataset",
                        resource_id=snap.id)
    db.commit()
    return {"ok": True, "id": str(snap.id), "code": snap.code, "row_count": snap.row_count}


@app.post("/api/intelligence/v1/datasets/{snapshot_id}/approve", dependencies=[Depends(management_auth)])
def approve_dataset(snapshot_id: uuid.UUID, request: Request, db: Session = Depends(db)):
    snap = quality_service.approve_dataset(db, snapshot_id, approved_by=_actor(request) or "system")
    db.commit()
    return {"ok": True, "id": str(snap.id), "state": snap.state}


# =====================================================================
# Features
# =====================================================================
@app.get("/api/intelligence/v1/features", dependencies=[Depends(management_auth)])
def list_features(db: Session = Depends(db)):
    return [{"id": str(f.id), "name": f.name, "version": f.version, "entity_key": f.entity_key,
             "data_type": f.data_type, "freshness_seconds": f.freshness_seconds,
             "pii_class": f.pii_class, "availability": f.availability, "owner": f.domain_owner}
            for f in db.query(FeatureDefinition).all()]


@app.post("/api/intelligence/v1/features", dependencies=[Depends(management_auth)])
def create_feature(payload: schemas.FeatureIn, db: Session = Depends(db)):
    row = FeatureDefinition(name=payload.name, version=payload.version,
                            domain_owner=payload.domain_owner, source_contract=payload.source_contract,
                            entity_key=payload.entity_key, data_type=payload.data_type,
                            freshness_seconds=payload.freshness_seconds, pii_class=payload.pii_class,
                            valid_range=payload.valid_range, default_value=payload.default_value,
                            missing_behavior="DEFAULT", availability=payload.availability,
                            transformation_version="v1", is_active=True)
    db.add(row)
    db.commit()
    return {"ok": True, "id": str(row.id), "name": row.name}


@app.get("/api/intelligence/v1/features/values", dependencies=[Depends(management_auth)])
def feature_values(entity_type: str, entity_ref: str, db: Session = Depends(db)):
    from app.models import OnlineFeatureValue
    rows = db.query(OnlineFeatureValue).filter(
        OnlineFeatureValue.tenant_id == _tid(),
        OnlineFeatureValue.entity_ref == entity_ref).all()
    return {"entity_ref": entity_ref,
            "values": {r.feature_name: r.value for r in rows},
            "quality": {r.feature_name: r.quality for r in rows}}


# =====================================================================
# MLOps: training, registry, deploy, monitoring
# =====================================================================
@app.post("/api/intelligence/v1/training", dependencies=[Depends(management_auth)])
def start_training(payload: schemas.TrainingConfigIn, db: Session = Depends(db)):
    result = ml_service.train_and_register(db, tenant_id=_tid(), model_code=payload.model_code,
                                           snapshot_id=payload.snapshot_id,
                                           config=payload.model_dump(), owner=payload.owner,
                                           purpose=payload.purpose,
                                           source_revision=payload.source_revision)
    db.commit()
    return result


@app.get("/api/intelligence/v1/training", dependencies=[Depends(management_auth)])
def list_training(db: Session = Depends(db)):
    return report_service.training_history(db, _tid())


@app.get("/api/intelligence/v1/models", dependencies=[Depends(management_auth)])
def list_models(db: Session = Depends(db)):
    rows = db.query(MlModel).order_by(MlModel.model_code, MlModel.version.desc()).all()
    return [{"id": str(m.id), "model_code": m.model_code, "version": m.version, "use_case": m.use_case,
             "algorithm": m.algorithm, "approval_status": m.approval_status,
             "deployment_status": m.deployment_status, "state": m.state,
             "decision_threshold": m.decision_threshold, "artifact_checksum": m.artifact_checksum,
             "applicable_scope": m.applicable_scope} for m in rows]


@app.post("/api/intelligence/v1/models/{model_id}/approve", dependencies=[Depends(management_auth)])
def approve_model(model_id: uuid.UUID, request: Request, db: Session = Depends(db)):
    model = ml_service.approve_model(db, model_id, approved_by=_actor(request) or "system")
    audit_service.outbox(db, "ai.model_approved.v1", model.tenant_id, None,
                         {"model_id": str(model.id)}, idempotency_key=f"model-approved:{model.id}")
    db.commit()
    return {"ok": True, "id": str(model.id), "approval_status": model.approval_status}


@app.post("/api/intelligence/v1/models/{model_id}/deploy", dependencies=[Depends(management_auth)])
def deploy_model(model_id: uuid.UUID, payload: schemas.DeployIn, request: Request, db: Session = Depends(db)):
    deployment = ml_service.deploy_model(db, model_id, payload.environment,
                                         traffic_percent=payload.traffic_percent, actor=_actor(request))
    model = ml_service.get_model(db, model_id)
    audit_service.outbox(db, "ai.model_deployed.v1", model.tenant_id, None,
                         {"model_id": str(model.id), "environment": deployment.environment},
                         idempotency_key=f"model-deployed:{model.id}:{deployment.environment}")
    db.commit()
    return {"ok": True, "deployment_id": str(deployment.id), "environment": deployment.environment}


@app.post("/api/intelligence/v1/models/{model_id}/rollback", dependencies=[Depends(management_auth)])
def rollback_model(model_id: uuid.UUID, request: Request, db: Session = Depends(db)):
    model = ml_service.rollback_model(db, model_id, actor=_actor(request))
    audit_service.outbox(db, "ai.model_rolled_back.v1", model.tenant_id, None,
                         {"model_id": str(model.id), "rollback_target": model.rollback_target},
                         idempotency_key=f"model-rolled-back:{model.id}")
    db.commit()
    return {"ok": True, "id": str(model.id), "state": model.state}


@app.post("/api/intelligence/v1/models/{model_id}/retire", dependencies=[Depends(management_auth)])
def retire_model(model_id: uuid.UUID, request: Request, db: Session = Depends(db)):
    model = ml_service.retire_model(db, model_id, actor=_actor(request))
    db.commit()
    return {"ok": True, "id": str(model.id), "state": model.state}


@app.post("/api/intelligence/v1/models/{model_id}/monitor", dependencies=[Depends(management_auth)])
def record_monitor(model_id: uuid.UUID, payload: schemas.MonitorIn, db: Session = Depends(db)):
    row = ml_service.record_monitor(db, tenant_id=_tid(), model_id=model_id,
                                    metric_type=payload.metric_type, value=payload.value,
                                    detail=payload.detail, alert=payload.alert)
    db.commit()
    return {"ok": True, "monitor_id": str(row.id), "alert": row.alert}


@app.get("/api/intelligence/v1/monitoring", dependencies=[Depends(management_auth)])
def monitoring(db: Session = Depends(db)):
    return report_service.model_health(db, _tid())


@app.post("/api/intelligence/v1/models/{model_id}/drift", dependencies=[Depends(management_auth)])
def detect_drift(model_id: uuid.UUID, payload: dict, db: Session = Depends(db)):
    row = ml_service.detect_drift(db, model_id, expected_mean=payload.get("expected_mean", 0.5),
                                  observed_mean=payload.get("observed_mean", 0.0),
                                  threshold=payload.get("threshold", 0.2))
    db.commit()
    return {"ok": True, "monitor_id": str(row.id), "value": row.value, "alert": row.alert}


# =====================================================================
# Fraud
# =====================================================================
@app.get("/api/intelligence/v1/fraud/rules", dependencies=[Depends(management_auth)])
def list_fraud_rules(db: Session = Depends(db)):
    return [{"id": str(r.id), "code": r.code, "version": r.version, "name": r.name,
             "severity": r.severity, "is_active": r.is_active, "condition": r.condition}
            for r in db.query(FraudRule).all()]


@app.post("/api/intelligence/v1/fraud/evaluate", dependencies=[Depends(management_auth)])
def evaluate_fraud(payload: schemas.FraudEvalIn, db: Session = Depends(db)):
    tenant = _tid(payload.tenant_id)
    signals = fraud_service.evaluate_rules(db, tenant_id=tenant, subject_type=payload.subject_type,
                                           subject=payload.subject, record=payload.record,
                                           model_score=payload.model_score,
                                           correlation_id=payload.correlation_id)
    db.commit()
    return [{"id": str(s.id), "risk_score": s.risk_score, "severity": s.severity,
             "factors": s.factors, "rule_code": s.rule_code} for s in signals]


@app.get("/api/intelligence/v1/fraud/signals", dependencies=[Depends(management_auth)])
def list_fraud_signals(state: Optional[str] = None, db: Session = Depends(db)):
    q = db.query(__import__("app.models", fromlist=["FraudSignal"]).FraudSignal).filter(
        __import__("app.models", fromlist=["FraudSignal"]).FraudSignal.tenant_id == _tid())
    if state:
        q = q.filter(__import__("app.models", fromlist=["FraudSignal"]).FraudSignal.state == state)
    rows = q.order_by(__import__("app.models", fromlist=["FraudSignal"]).FraudSignal.detection_time.desc()).all()
    return [{"id": str(s.id), "subject": s.subject, "subject_type": s.subject_type,
             "risk_score": s.risk_score, "severity": s.severity, "state": s.state,
             "factors": s.factors, "detection_time": s.detection_time.isoformat()} for s in rows]


@app.post("/api/intelligence/v1/fraud/cases", dependencies=[Depends(management_auth)])
def open_fraud_case(payload: dict, db: Session = Depends(db)):
    from app.models import FraudSignal
    signals = db.query(FraudSignal).filter(FraudSignal.id.in_(_uuid(x) for x in payload.get("signal_ids", []))).all()
    case = fraud_service.open_case(db, tenant_id=_tid(), subject_type=payload["subject_type"],
                                   subject=payload["subject"], signals=signals)
    db.commit()
    return {"ok": True, "case_id": str(case.id), "risk_score": case.risk_score, "state": case.state}


@app.get("/api/intelligence/v1/fraud/cases", dependencies=[Depends(management_auth)])
def list_fraud_cases(state: Optional[str] = None, db: Session = Depends(db)):
    q = db.query(FraudCase).filter(FraudCase.tenant_id == _tid())
    if state:
        q = q.filter(FraudCase.state == state)
    rows = q.order_by(FraudCase.opened_at.desc()).all()
    return [{"id": str(c.id), "subject": c.subject, "subject_type": c.subject_type,
             "risk_score": c.risk_score, "severity": c.severity, "state": c.state,
             "decision": c.decision, "final_outcome": c.final_outcome} for c in rows]


@app.post("/api/intelligence/v1/fraud/cases/{case_id}/decision", dependencies=[Depends(management_auth)])
def decide_fraud_case(case_id: uuid.UUID, payload: schemas.FraudDecisionIn, request: Request, db: Session = Depends(db)):
    case = fraud_service.decide_case(db, case_id, decision=payload.decision, reason=payload.reason,
                                     actor=_actor(request))
    db.commit()
    return {"ok": True, "case_id": str(case.id), "decision": case.decision}


@app.post("/api/intelligence/v1/fraud/cases/{case_id}/transition", dependencies=[Depends(management_auth)])
def transition_fraud_case(case_id: uuid.UUID, payload: dict, request: Request, db: Session = Depends(db)):
    case = fraud_service.transition(db, case_id, payload["target"], actor=_actor(request))
    db.commit()
    return {"ok": True, "case_id": str(case.id), "state": case.state}


@app.post("/api/intelligence/v1/fraud/cases/{case_id}/recommend", dependencies=[Depends(management_auth)])
def recommend_fraud_action(case_id: uuid.UUID, payload: schemas.FraudActionIn, db: Session = Depends(db)):
    row = fraud_service.recommend_action(db, case_id, action_type=payload.action_type,
                                         target_service=payload.target_service, rationale=payload.rationale)
    db.commit()
    return {"ok": True, "recommendation_id": str(row.id)}


# =====================================================================
# Churn & retention
# =====================================================================
@app.post("/api/intelligence/v1/churn/score", dependencies=[Depends(management_auth)])
def score_churn(payload: schemas.ChurnScoreIn, db: Session = Depends(db)):
    row = churn_service.score_customer(db, tenant_id=_tid(), customer_ref=payload.customer_ref,
                                       service_ref=payload.service_ref,
                                       horizon_days=payload.horizon_days,
                                       model_code=payload.model_code)
    db.commit()
    return {"churn_score_id": str(row.id), "customer_ref": row.customer_ref, "score": row.score,
            "risk_band": row.risk_band, "confidence": row.confidence, "top_drivers": row.top_drivers,
            "recommended_action": row.recommended_action, "model_version": row.model_version}


@app.get("/api/intelligence/v1/churn", dependencies=[Depends(management_auth)])
def list_churn(risk_band: Optional[str] = None, db: Session = Depends(db)):
    q = db.query(ChurnScore).filter(ChurnScore.tenant_id == _tid(), ChurnScore.state == "ACTIVE")
    if risk_band:
        q = q.filter(ChurnScore.risk_band == risk_band)
    rows = q.order_by(ChurnScore.score.desc()).all()
    return [{"id": str(r.id), "customer_ref": r.customer_ref, "score": r.score,
             "risk_band": r.risk_band, "horizon_days": r.horizon_days,
             "recommended_action": r.recommended_action, "top_drivers": r.top_drivers,
             "expiry_at": r.expiry_at.isoformat()} for r in rows]


@app.post("/api/intelligence/v1/retention/candidates", dependencies=[Depends(management_auth)])
def create_retention_candidate(payload: dict, db: Session = Depends(db)):
    row = churn_service.create_retention_candidate(db, _uuid(payload["churn_score_id"]),
                                                   recommended_action=payload.get("recommended_action"))
    db.commit()
    return {"ok": True, "candidate_id": str(row.id)}


@app.post("/api/intelligence/v1/retention/candidates/{candidate_id}/track", dependencies=[Depends(management_auth)])
def track_offer(candidate_id: uuid.UUID, payload: schemas.RetentionTrackIn, db: Session = Depends(db)):
    row = churn_service.track_offer(db, candidate_id, presented=payload.presented,
                                    consent=payload.consent, accepted=payload.accepted,
                                    outcome=payload.outcome, experiment_id=payload.experiment_id)
    db.commit()
    return {"ok": True, "candidate_id": str(row.id), "offer_presented": row.offer_presented}


# =====================================================================
# Maintenance & capacity
# =====================================================================
@app.post("/api/intelligence/v1/maintenance/predict", dependencies=[Depends(management_auth)])
def predict_failure(payload: schemas.FailurePredictionIn, db: Session = Depends(db)):
    row = maintenance_service.predict_failure(db, tenant_id=_tid(), asset_type=payload.asset_type,
                                              asset_ref=payload.asset_ref,
                                              model_code=payload.model_code,
                                              horizon_days=payload.horizon_days)
    db.commit()
    return {"prediction_id": str(row.id), "asset_ref": row.asset_ref,
            "failure_probability": row.failure_probability, "degradation_risk": row.degradation_risk,
            "recommendation_type": row.recommendation_type, "model_version": row.model_version}


@app.get("/api/intelligence/v1/maintenance", dependencies=[Depends(management_auth)])
def list_failure_predictions(db: Session = Depends(db)):
    from app.models import FailurePrediction
    rows = db.query(FailurePrediction).filter(
        FailurePrediction.tenant_id == _tid(), FailurePrediction.state == "ACTIVE").all()
    return [{"id": str(r.id), "asset_type": r.asset_type, "asset_ref": r.asset_ref,
             "failure_probability": r.failure_probability, "degradation_risk": r.degradation_risk,
             "recommendation_type": r.recommendation_type,
             "expiry_at": r.expiry_at.isoformat()} for r in rows]


@app.post("/api/intelligence/v1/capacity/forecast", dependencies=[Depends(management_auth)])
def forecast_capacity(payload: schemas.CapacityForecastIn, db: Session = Depends(db)):
    row = maintenance_service.forecast_capacity(db, tenant_id=_tid(),
                                                resource_type=payload.resource_type,
                                                resource_ref=payload.resource_ref,
                                                utilization_series=payload.utilization_series,
                                                horizon_days=payload.horizon_days,
                                                model_code=payload.model_code)
    db.commit()
    return {"forecast_id": str(row.id), "resource_ref": row.resource_ref, "risk": row.risk,
            "forecast": row.forecast, "confidence_interval": row.confidence_interval}


@app.get("/api/intelligence/v1/capacity", dependencies=[Depends(management_auth)])
def list_capacity(db: Session = Depends(db)):
    from app.models import CapacityForecast
    rows = db.query(CapacityForecast).filter(CapacityForecast.tenant_id == _tid()).all()
    return [{"id": str(r.id), "resource_type": r.resource_type, "resource_ref": r.resource_ref,
             "risk": r.risk, "forecast": r.forecast, "computed_at": r.computed_at.isoformat()} for r in rows]


# =====================================================================
# Recommendations
# =====================================================================
@app.post("/api/intelligence/v1/recommendations", dependencies=[Depends(management_auth)])
def create_recommendation(payload: schemas.RecommendationIn, db: Session = Depends(db)):
    row = remediation_service.create_recommendation(
        db, tenant_id=_tid(), kind=payload.kind, subject_type=payload.subject_type,
        subject=payload.subject, summary=payload.summary, evidence=payload.evidence,
        autonomy_level=payload.autonomy_level, model_code=payload.model_code,
        model_version=payload.model_version, confidence=payload.confidence,
        expected_impact=payload.expected_impact, expires_hours=payload.expires_hours)
    db.commit()
    return {"ok": True, "recommendation_id": str(row.id), "autonomy_level": row.autonomy_level}


@app.get("/api/intelligence/v1/recommendations", dependencies=[Depends(management_auth)])
def list_recommendations(state: Optional[str] = None, kind: Optional[str] = None, db: Session = Depends(db)):
    q = db.query(Recommendation).filter(Recommendation.tenant_id == _tid())
    if state:
        q = q.filter(Recommendation.state == state)
    if kind:
        q = q.filter(Recommendation.kind == kind)
    rows = q.order_by(Recommendation.created_at.desc()).all()
    return [{"id": str(r.id), "kind": r.kind, "subject": r.subject, "summary": r.summary,
             "autonomy_level": r.autonomy_level, "state": r.state, "confidence": r.confidence,
             "model_code": r.model_code, "model_version": r.model_version} for r in rows]


# =====================================================================
# Remediation
# =====================================================================
@app.get("/api/intelligence/v1/remediation/policies", dependencies=[Depends(management_auth)])
def list_remediation_policies(db: Session = Depends(db)):
    return [{"id": str(p.id), "code": p.code, "action_type": p.action_type,
             "autonomy_level": p.autonomy_level, "approval_required": p.approval_required,
             "action_budget": p.action_budget, "rate_limit_per_hour": p.rate_limit_per_hour,
             "cooldown_seconds": p.cooldown_seconds, "max_blast_radius": p.max_blast_radius,
             "reversible": p.reversible, "enabled": p.enabled} for p in db.query(RemediationPolicy).all()]


@app.post("/api/intelligence/v1/remediation/intents", dependencies=[Depends(management_auth)])
def create_remediation_intent(payload: schemas.RemediationIntentIn, request: Request, db: Session = Depends(db)):
    intent = remediation_service.create_remediation_intent(
        db, tenant_id=_tid(), policy_code=payload.policy_code, target_type=payload.target_type,
        target_ref=payload.target_ref, payload=payload.payload,
        correlation_id=payload.correlation_id, causation_id=payload.causation_id,
        idempotency_key=payload.idempotency_key, requested_by=_actor(request))
    db.commit()
    return {"ok": True, "intent_id": str(intent.id), "state": intent.state,
            "autonomy_level": intent.autonomy_level}


@app.get("/api/intelligence/v1/remediation/intents", dependencies=[Depends(management_auth)])
def list_remediation_intents(state: Optional[str] = None, db: Session = Depends(db)):
    q = db.query(RemediationIntent).filter(RemediationIntent.tenant_id == _tid())
    if state:
        q = q.filter(RemediationIntent.state == state)
    rows = q.order_by(RemediationIntent.requested_at.desc()).all()
    return [{"id": str(r.id), "policy_code": r.policy_code, "action_type": r.action_type,
             "target_ref": r.target_ref, "autonomy_level": r.autonomy_level, "state": r.state,
             "budget_used": r.budget_used, "attempt": r.attempt,
             "requested_at": r.requested_at.isoformat()} for r in rows]


@app.post("/api/intelligence/v1/remediation/intents/{intent_id}/approve", dependencies=[Depends(management_auth)])
def approve_remediation(intent_id: uuid.UUID, payload: schemas.ApprovalIn, db: Session = Depends(db)):
    intent = remediation_service.approve_intent(db, intent_id, approver=payload.approver,
                                                reason=payload.reason)
    db.commit()
    return {"ok": True, "intent_id": str(intent.id), "state": intent.state}


@app.post("/api/intelligence/v1/remediation/intents/{intent_id}/reject", dependencies=[Depends(management_auth)])
def reject_remediation(intent_id: uuid.UUID, payload: schemas.ApprovalIn, db: Session = Depends(db)):
    intent = remediation_service.reject_intent(db, intent_id, approver=payload.approver,
                                               reason=payload.reason)
    db.commit()
    return {"ok": True, "intent_id": str(intent.id), "state": intent.state}


@app.post("/api/intelligence/v1/remediation/intents/{intent_id}/execute", dependencies=[Depends(management_auth)])
def execute_remediation(intent_id: uuid.UUID, request: Request, db: Session = Depends(db)):
    intent = remediation_service.execute_intent(db, intent_id, executor=_actor(request))
    db.commit()
    return {"ok": True, "intent_id": str(intent.id), "state": intent.state}


@app.post("/api/intelligence/v1/remediation/intents/{intent_id}/complete", dependencies=[Depends(management_auth)])
def complete_remediation(intent_id: uuid.UUID, payload: dict, db: Session = Depends(db)):
    intent = remediation_service.complete_intent(db, intent_id, result=payload.get("result", "SUCCESS"),
                                                 verification=payload.get("verification"),
                                                 detail=payload.get("detail"))
    db.commit()
    return {"ok": True, "intent_id": str(intent.id), "state": intent.state}


@app.post("/api/intelligence/v1/remediation/intents/{intent_id}/fail", dependencies=[Depends(management_auth)])
def fail_remediation(intent_id: uuid.UUID, payload: dict, db: Session = Depends(db)):
    intent = remediation_service.fail_intent(db, intent_id, detail=payload.get("detail"),
                                             compensate=payload.get("compensate", False))
    db.commit()
    return {"ok": True, "intent_id": str(intent.id), "state": intent.state}


@app.get("/api/intelligence/v1/kill-switch", dependencies=[Depends(management_auth)])
def get_kill_switch(db: Session = Depends(db)):
    ctx = get_context()
    tid = ctx.tenant_id if ctx else None
    global_on, tenant_on = remediation_service.get_kill_switches(db, tid)
    return {"global": global_on, "tenant": tenant_on}


@app.post("/api/intelligence/v1/kill-switch", dependencies=[Depends(management_auth)])
def set_kill_switch(payload: schemas.KillSwitchIn, request: Request, db: Session = Depends(db)):
    tid = _tid() if payload.scope == "TENANT" else None
    row = remediation_service.set_kill_switch(db, scope=payload.scope, tenant_id=tid,
                                              enabled=payload.enabled, reason=payload.reason,
                                              actor=_actor(request))
    db.commit()
    return {"ok": True, "kill_switch_id": str(row.id), "scope": row.scope, "enabled": row.enabled}


# =====================================================================
# Insights & reports
# =====================================================================
@app.get("/api/intelligence/v1/insights", dependencies=[Depends(management_auth)])
def tenant_insights(days: int = 7, db: Session = Depends(db)):
    ctx = require_tenant()
    return report_service.tenant_insights(db, ctx.tenant_id, days=days)


@app.get("/api/intelligence/v1/reports/executive", dependencies=[Depends(management_auth)])
def executive_report(days: int = 7, db: Session = Depends(db)):
    require_platform_aggregate()
    return report_service.executive_dashboard(db, days=days)


@app.get("/api/intelligence/v1/audit-log", dependencies=[Depends(management_auth)])
def audit_log(limit: int = 100, db: Session = Depends(db)):
    from app.models import AuditLog
    rows = db.query(AuditLog).order_by(AuditLog.occurred_at.desc()).limit(limit).all()
    return [{"id": str(r.id), "actor": r.actor, "action": r.action, "resource_type": r.resource_type,
             "resource_id": r.resource_id, "tenant_id": str(r.tenant_id) if r.tenant_id else None,
             "occurred_at": r.occurred_at.isoformat()} for r in rows]


# ===========================================================================
# Operations intelligence (Batch 7b: 889, 1289, 1297, 1420, 1481)
# ===========================================================================

@app.post("/api/intelligence/v1/ops/personalization/profiles", dependencies=[Depends(management_auth)])
def upsert_personalization(payload: dict, request: Request, db: Session = Depends(db)):
    row = operations_service.PersonalizationService.upsert(db, _tid(), payload, actor=_actor(request))
    return {"id": str(row.id), "subscriber_id": row.subscriber_id, "segments": row.segments,
            "engagement_score": row.engagement_score}


@app.post("/api/intelligence/v1/ops/personalization/recommend", dependencies=[Depends(management_auth)])
def recommend_personalization(payload: dict, db: Session = Depends(db)):
    return operations_service.PersonalizationService.recommend(db, _tid(), payload.get("subscriber_id"))


@app.post("/api/intelligence/v1/ops/bottlenecks/detect", dependencies=[Depends(management_auth)])
def detect_bottleneck(payload: dict, request: Request, db: Session = Depends(db)):
    row = operations_service.BottleneckService.detect(
        db, _tid(), payload.get("scope"), payload.get("metric"),
        float(payload.get("value", 0)), float(payload.get("threshold", 0)), actor=_actor(request))
    if row is None:
        return {"detected": False, "scope": payload.get("scope")}
    return {"detected": True, "id": str(row.id), "scope": row.scope, "metric": row.metric,
            "severity": row.severity}


@app.get("/api/intelligence/v1/ops/bottlenecks", dependencies=[Depends(management_auth)])
def list_bottlenecks(status: str | None = None, db: Session = Depends(db)):
    from app.models import Bottleneck
    q = db.query(Bottleneck).filter(Bottleneck.tenant_id == _tid())
    if status:
        q = q.filter(Bottleneck.status == status)
    return [{"id": str(b.id), "scope": b.scope, "metric": b.metric, "severity": b.severity,
             "status": b.status, "detected_at": b.detected_at} for b in q.order_by(Bottleneck.detected_at.desc()).all()]


@app.post("/api/intelligence/v1/ops/bottlenecks/{bottleneck_id}/resolve", dependencies=[Depends(management_auth)])
def resolve_bottleneck(bottleneck_id: uuid.UUID, db: Session = Depends(db)):
    try:
        row = operations_service.BottleneckService.resolve(db, _tid(), bottleneck_id)
    except KeyError:
        raise HTTPException(404, "bottleneck not found")
    return {"id": str(row.id), "status": row.status}


@app.post("/api/intelligence/v1/ops/automation-coverage", dependencies=[Depends(management_auth)])
def compute_automation_coverage(payload: dict, request: Request, db: Session = Depends(db)):
    row = operations_service.CoverageService.compute(
        db, _tid(), payload.get("period", "MONTH"), int(payload.get("automated", 0)),
        int(payload.get("manual", 0)), actor=_actor(request))
    return {"id": str(row.id), "period": row.period, "coverage_pct": row.coverage_pct}


@app.get("/api/intelligence/v1/ops/automation-coverage", dependencies=[Depends(management_auth)])
def get_automation_coverage(period: str = "MONTH", db: Session = Depends(db)):
    from app.models import AutomationCoverage
    row = db.query(AutomationCoverage).filter(
        AutomationCoverage.tenant_id == _tid(), AutomationCoverage.period == period).first()
    if not row:
        raise HTTPException(404, "coverage not computed")
    return {"period": row.period, "automated": row.automated_count, "manual": row.manual_count,
            "coverage_pct": row.coverage_pct}


@app.post("/api/intelligence/v1/ops/node-profit", dependencies=[Depends(management_auth)])
def record_node_profit(payload: dict, request: Request, db: Session = Depends(db)):
    row = operations_service.ProfitabilityService.node_profit(db, _tid(), payload, actor=_actor(request))
    return {"id": str(row.id), "node": row.node, "revenue": row.revenue, "cost": row.cost,
            "profit": row.profit}


@app.get("/api/intelligence/v1/ops/node-profit", dependencies=[Depends(management_auth)])
def list_node_profit(db: Session = Depends(db)):
    from app.models import NodeProfit
    rows = db.query(NodeProfit).filter(NodeProfit.tenant_id == _tid()).order_by(NodeProfit.profit.desc()).all()
    return [{"id": str(r.id), "node": r.node, "period": r.period, "profit": r.profit} for r in rows]


@app.post("/api/intelligence/v1/ops/region-profitability", dependencies=[Depends(management_auth)])
def record_region_profitability(payload: dict, request: Request, db: Session = Depends(db)):
    row = operations_service.ProfitabilityService.region_profitability(db, _tid(), payload, actor=_actor(request))
    return {"id": str(row.id), "region": row.region, "revenue": row.revenue, "cost": row.cost,
            "profit_margin": row.profit_margin}


@app.get("/api/intelligence/v1/ops/region-profitability", dependencies=[Depends(management_auth)])
def list_region_profitability(db: Session = Depends(db)):
    from app.models import RegionProfitability
    rows = db.query(RegionProfitability).filter(
        RegionProfitability.tenant_id == _tid()).order_by(RegionProfitability.profit_margin.desc()).all()
    return [{"id": str(r.id), "region": r.region, "period": r.period,
             "profit_margin": r.profit_margin} for r in rows]


# ---------------------------------------------------------------------------
# Aiops advanced (Batch 8h: 731, 739, 861, 871, 883, 886, 888, 898)
# ---------------------------------------------------------------------------

@app.post("/api/intelligence/v1/aiops/network-twin", dependencies=[Depends(management_auth)])
def create_network_twin(payload: dict, request: Request, db: Session = Depends(db)):
    row = aiops_advanced_service.AiopsAdvancedService.create_network_twin(db, _tid(), payload,
                                                                          actor=_actor(request))
    return {"id": str(row.id), "twin_name": row.twin_name}


@app.get("/api/intelligence/v1/aiops/network-twin", dependencies=[Depends(management_auth)])
def list_network_twin(db: Session = Depends(db)):
    from app.models import NetworkTwin
    rows = db.query(NetworkTwin).filter(NetworkTwin.tenant_id == _tid()).all()
    return [{"id": str(r.id), "twin_name": r.twin_name} for r in rows]


@app.post("/api/intelligence/v1/aiops/scaling", dependencies=[Depends(management_auth)])
def autonomous_scale(payload: dict, request: Request, db: Session = Depends(db)):
    row = aiops_advanced_service.AiopsAdvancedService.autonomous_scale(db, _tid(), payload,
                                                                       actor=_actor(request))
    return {"id": str(row.id), "service": row.service, "action": row.action, "reason": row.reason}


@app.post("/api/intelligence/v1/aiops/pricing", dependencies=[Depends(management_auth)])
def change_price(payload: dict, request: Request, db: Session = Depends(db)):
    row = aiops_advanced_service.AiopsAdvancedService.change_price(db, _tid(), payload,
                                                                   actor=_actor(request))
    return {"id": str(row.id), "product": row.product, "old_price": row.old_price,
            "new_price": row.new_price}


@app.post("/api/intelligence/v1/aiops/business-twin", dependencies=[Depends(management_auth)])
def create_business_twin(payload: dict, request: Request, db: Session = Depends(db)):
    row = aiops_advanced_service.AiopsAdvancedService.create_business_twin(db, _tid(), payload,
                                                                           actor=_actor(request))
    return {"id": str(row.id), "twin_name": row.twin_name, "scenario": row.scenario}


@app.post("/api/intelligence/v1/aiops/upsell", dependencies=[Depends(management_auth)])
def suggest_upsell(payload: dict, request: Request, db: Session = Depends(db)):
    row = aiops_advanced_service.AiopsAdvancedService.suggest_upsell(db, _tid(), payload,
                                                                     actor=_actor(request))
    return {"id": str(row.id), "customer_id": row.customer_id, "product": row.product}


@app.post("/api/intelligence/v1/aiops/voice", dependencies=[Depends(management_auth)])
def voice_respond(payload: dict, request: Request, db: Session = Depends(db)):
    row = aiops_advanced_service.AiopsAdvancedService.voice_respond(db, _tid(), payload,
                                                                    actor=_actor(request))
    return {"id": str(row.id), "response": row.response}


@app.post("/api/intelligence/v1/aiops/sentiment", dependencies=[Depends(management_auth)])
def handle_sentiment(payload: dict, request: Request, db: Session = Depends(db)):
    row = aiops_advanced_service.AiopsAdvancedService.handle_sentiment(db, _tid(), payload,
                                                                       actor=_actor(request))
    return {"id": str(row.id), "sentiment": row.sentiment, "action": row.action}


@app.post("/api/intelligence/v1/aiops/workforce", dependencies=[Depends(management_auth)])
def automate_workforce(payload: dict, request: Request, db: Session = Depends(db)):
    row = aiops_advanced_service.AiopsAdvancedService.automate_workforce(db, _tid(), payload,
                                                                         actor=_actor(request))
    return {"id": str(row.id), "task_name": row.task_name, "automation_pct": row.automation_pct,
            "status": row.status}
