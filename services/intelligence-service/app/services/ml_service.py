"""MLOps lifecycle: training runs, registry, model cards, deploy/rollback,
monitoring/drift."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain import statistics as stats
from ..domain.exceptions import ModelError, ModelImmutableError, NotFoundError
from ..models import (MlModel, ModelCard, ModelDeployment, ModelMonitor, TrainingRun)
from ..state_machine import guarded as model_guarded
from .audit_service import audit
from .quality_service import snapshot_rows


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _latest_model(session: Session, model_code: str) -> MlModel:
    return session.execute(select(MlModel).where(MlModel.model_code == model_code)
                           .order_by(MlModel.version.desc()).limit(1)).scalars().first()


def _json_safe(value):
    """Recursively convert UUID/datetime objects to JSON-serializable strings."""
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def create_training_run(session: Session, *, tenant_id, model_code: str, snapshot_id: uuid.UUID,
                        config: dict, source_revision: str | None = None) -> TrainingRun:
    run = TrainingRun(tenant_id=tenant_id, run_id=f"run-{uuid.uuid4().hex[:12]}",
                      model_code=model_code, dataset_snapshot_id=snapshot_id,
                      feature_set_version=config.get("feature_set_version"),
                      config=_json_safe(config), source_revision=source_revision,
                      algorithm=config.get("algorithm", "WEIGHTED_LOGIT"),
                      split_scheme=config.get("split_scheme", "TIME_BASED"),
                      state="RUNNING", started_at=_now())
    session.add(run)
    session.flush()
    return run


def _train_dataset(session: Session, snapshot_id: uuid.UUID, label_field: str = "churned"):
    """Build (features, labels) from a snapshot. Time-based split at 80%."""
    rows = snapshot_rows(session, snapshot_id)
    samples = []
    for r in rows:
        normalized = r.normalized
        features = {k: v for k, v in normalized.items() if isinstance(v, (int, float))}
        label = 1 if normalized.get(label_field, 0) else 0
        samples.append({"features": features, "label": label, "event_time": r.event_time})
    samples.sort(key=lambda s: s["event_time"])
    split = max(1, int(len(samples) * 0.8))
    return samples[:split], samples[split:]


def _leakage_check(samples: list[dict]) -> dict:
    """Heuristic leakage check: target label must not appear as a feature."""
    leaked = []
    for s in samples[:500]:
        if s.get("label", 0) in (0, 1) and "label" in s.get("features", {}):
            leaked.append("label-feature")
        if "outcome" in s.get("features", {}):
            leaked.append("outcome-feature")
    return {"leaked_features": sorted(set(leaked)), "leakage": len(leaked) > 0}


def train_and_register(session: Session, *, tenant_id, model_code: str, snapshot_id: uuid.UUID,
                       config: dict, source_revision: str | None = None,
                       owner: str | None = None, purpose: str | None = None) -> dict:
    """Train a baseline model, compute metrics vs a naive baseline, register a
    new model version with a model card. No pickle — artifact is JSON config."""
    run = create_training_run(session, tenant_id=tenant_id, model_code=model_code,
                              snapshot_id=snapshot_id, config=config,
                              source_revision=source_revision)
    train, test = _train_dataset(session, snapshot_id)
    leakage = _leakage_check(train)
    run.leakage_checked = True
    algorithm = config.get("algorithm", "WEIGHTED_LOGIT")
    params = config.get("parameters", {})
    feature_names = config.get("feature_names", [])
    threshold = float(config.get("decision_threshold", 0.5))

    # Predictions on the test set using the algorithm's scoring function.
    y_true = [s["label"] for s in test]
    y_score = []
    for s in test:
        if algorithm == "RULE_ENGINE":
            y_score.append(0.5 if sum(s["features"].get(k, 0.0) or 0.0 for k in feature_names) else 0.1)
        else:
            y_score.append(stats.weighted_logit(s["features"], params.get("weights", {}),
                                                params.get("intercept", 0.0)))
    if not y_true:
        metrics = {"error": "empty test set"}
        baseline = {}
    else:
        pr = stats.precision_recall_at_threshold(y_true, y_score, threshold)
        metrics = {
            "precision": pr["precision"], "recall": pr["recall"], "fpr": pr["fpr"],
            "pr_auc": stats.pr_auc(y_true, y_score),
            "roc_auc": stats.auc_roc(y_true, y_score),
            "ece": stats.expected_calibration_error(y_true, y_score),
            "n_test": len(y_true), "baseline_lift": _baseline_lift(y_true, y_score, threshold),
        }
        # Baseline: always-predict-most-common-class.
        positive_rate = sum(y_true) / len(y_true)
        baseline = {"always_positive_accuracy": positive_rate,
                    "baseline_precision": positive_rate,
                    "baseline_recall": positive_rate if positive_rate else 0.0}

    run.state = "SUCCEEDED"
    run.metrics = metrics
    run.finished_at = _now()
    session.flush()

    prev = _latest_model(session, model_code)
    version = (prev.version + 1) if prev else 1
    model = MlModel(
        tenant_id=tenant_id, model_code=model_code, version=version,
        name=config.get("name"), use_case=config.get("use_case", "CHURN"), purpose=purpose,
        owner=owner, algorithm=algorithm, parameters=params, feature_names=feature_names,
        feature_set_version=config.get("feature_set_version"),
        training_run_id=run.id, dataset_version=str(snapshot_id),
        training_window=config.get("training_window", {}),
        evaluation_metrics=metrics, baseline_metrics=baseline,
        decision_threshold=threshold, calibration=config.get("calibration", {}),
        explainability_method=config.get("explainability_method", "WEIGHTS"),
        artifact={"algorithm": algorithm, "parameters": params, "feature_names": feature_names,
                  "threshold": threshold},
        artifact_checksum=stats.checksum({"algorithm": algorithm, "parameters": params}),
        applicable_scope=config.get("applicable_scope", "GLOBAL_BASELINE"),
        approval_status="DRAFT", deployment_status="DRAFT", state="DRAFT",
        rollback_target=str(prev.id) if prev else None)
    session.add(model)
    session.flush()
    card = ModelCard(tenant_id=tenant_id, model_id=model.id, purpose=purpose or config.get("purpose"),
                     known_limitations=config.get("known_limitations", []),
                     intended_use=config.get("intended_use", []),
                     training_window=config.get("training_window", {}))
    session.add(card)
    session.flush()
    model.state = "EVALUATED"  # training produced evaluation metrics
    return {"model_id": str(model.id), "version": version, "run_id": run.run_id,
            "metrics": metrics, "leakage": leakage}


def _baseline_lift(y_true, y_score, threshold) -> float:
    """Ratio of model F1-ish score to a majority-class baseline F1."""
    pr = stats.precision_recall_at_threshold(y_true, y_score, threshold)
    pos = sum(y_true)
    total = len(y_true)
    if pos == 0 or pos == total:
        return 1.0
    model_f1 = 2 * pr["precision"] * pr["recall"] / max(pr["precision"] + pr["recall"], 1e-9)
    baseline_precision = pos / total
    baseline_f1 = 2 * baseline_precision * baseline_precision / max(2 * baseline_precision, 1e-9)
    return round(model_f1 / max(baseline_f1, 1e-9), 3)


def get_model(session: Session, model_id: uuid.UUID) -> MlModel:
    model = session.get(MlModel, model_id)
    if model is None:
        raise NotFoundError("model not found")
    return model


def transition_model(session: Session, model: MlModel, target: str):
    if target not in _MODEL_FLOW(model.state):
        raise ModelError(f"invalid model transition {model.state} -> {target}")
    model.state = target
    if target == "APPROVED":
        model.approval_status = "APPROVED"
    if target == "REJECTED":
        model.approval_status = "REJECTED"


def _MODEL_FLOW(state):
    return {
        "DRAFT": {"EVALUATED", "REJECTED"},
        "EVALUATED": {"PENDING_APPROVAL", "REJECTED", "DRAFT"},
        "PENDING_APPROVAL": {"APPROVED", "REJECTED", "DRAFT"},
        "APPROVED": {"SHADOW", "PRODUCTION", "RETIRED"},
        "SHADOW": {"CANARY", "PRODUCTION", "RETIRED"},
        "CANARY": {"PRODUCTION", "RETIRED"},
        "PRODUCTION": {"SHADOW", "ROLLED_BACK", "RETIRED"},
        "ROLLED_BACK": {"RETIRED", "SHADOW"},
        "RETIRED": {"ARCHIVED"},
        "ARCHIVED": set(),
        "REJECTED": set(),
    }.get(state, set())


def approve_model(session: Session, model_id: uuid.UUID, approved_by: str) -> MlModel:
    model = get_model(session, model_id)
    if model.state not in ("EVALUATED", "PENDING_APPROVAL"):
        raise ModelError(f"cannot approve model in state {model.state}")
    model.state = "APPROVED"
    model.approval_status = "APPROVED"
    model.approved_by = approved_by
    audit(session, model.tenant_id, approved_by, "model.approved", resource_type="model",
          resource_id=model.id)
    return model


def deploy_model(session: Session, model_id: uuid.UUID, environment: str, *,
                 traffic_percent: int = 100, actor: str | None = None) -> ModelDeployment:
    model = get_model(session, model_id)
    if model.state not in ("APPROVED", "SHADOW", "CANARY"):
        raise ModelError(f"only approved models can be deployed (state={model.state})")
    if environment not in ("SHADOW", "CANARY", "PRODUCTION"):
        raise ModelError("environment must be SHADOW, CANARY or PRODUCTION")
    if environment not in _MODEL_FLOW(model.state):
        raise ModelError(f"invalid deploy transition {model.state} -> {environment}")
    existing = session.scalars(select(ModelDeployment).where(
        ModelDeployment.model_id == model.id, ModelDeployment.environment == environment,
        ModelDeployment.status == "ACTIVE")).first()
    if existing is not None:
        return existing
    deployment = ModelDeployment(tenant_id=model.tenant_id, model_id=model.id,
                                 environment=environment, traffic_percent=traffic_percent,
                                 status="ACTIVE", started_at=_now(), detail={})
    session.add(deployment)
    model.state = environment
    model.deployment_status = environment
    session.flush()
    audit(session, model.tenant_id, actor, f"model.deploy.{environment.lower()}",
          resource_type="model", resource_id=model.id)
    return deployment


def rollback_model(session: Session, model_id: uuid.UUID, *, actor: str | None = None) -> MlModel:
    model = get_model(session, model_id)
    if model.state not in ("SHADOW", "CANARY", "PRODUCTION"):
        raise ModelError(f"cannot rollback model in state {model.state}")
    target = model.rollback_target
    model.state = "ROLLED_BACK"
    model.deployment_status = "ROLLED_BACK"
    for dep in session.scalars(select(ModelDeployment).where(
            ModelDeployment.model_id == model.id, ModelDeployment.status == "ACTIVE")):
        dep.status = "ENDED"
        dep.ended_at = _now()
    session.flush()
    audit(session, model.tenant_id, actor, "model.rolled_back", resource_type="model",
          resource_id=model.id, after={"rollback_target": target})
    return model


def retire_model(session: Session, model_id: uuid.UUID, *, actor: str | None = None) -> MlModel:
    model = get_model(session, model_id)
    if model.state not in ("APPROVED", "SHADOW", "CANARY", "PRODUCTION", "ROLLED_BACK"):
        raise ModelError(f"cannot retire model in state {model.state}")
    model.state = "RETIRED"
    model.deployment_status = "RETIRED"
    for dep in session.scalars(select(ModelDeployment).where(
            ModelDeployment.model_id == model.id, ModelDeployment.status == "ACTIVE")):
        dep.status = "ENDED"
        dep.ended_at = _now()
    session.flush()
    audit(session, model.tenant_id, actor, "model.retired", resource_type="model", resource_id=model.id)
    return model


def record_monitor(session: Session, *, tenant_id, model_id: uuid.UUID, metric_type: str,
                   value: float, detail: dict | None = None, alert: bool = False,
                   window_minutes: int = 60) -> ModelMonitor:
    now = _now()
    row = ModelMonitor(tenant_id=tenant_id, model_id=model_id, metric_type=metric_type,
                       value=value, detail=detail or {}, window_start=now - timedelta(minutes=window_minutes),
                       window_end=now, alert=alert)
    session.add(row)
    session.flush()
    return row


def detect_drift(session: Session, model_id: uuid.UUID, *, expected_mean: float,
                 observed_mean: float, threshold: float = 0.2, window_minutes: int = 60) -> ModelMonitor:
    """Simple drift: normalized absolute deviation of observed vs expected mean."""
    value = abs(observed_mean - expected_mean) / max(abs(expected_mean), 1e-9)
    alert = value >= threshold
    model = get_model(session, model_id)
    return record_monitor(session, tenant_id=model.tenant_id, model_id=model_id,
                          metric_type="prediction_drift", value=round(value, 4),
                          detail={"expected_mean": expected_mean, "observed_mean": observed_mean},
                          alert=alert, window_minutes=window_minutes)
