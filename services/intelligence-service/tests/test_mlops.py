"""MLOps lifecycle: reproducible training, registry, approval, deploy,
rollback, monitoring/drift."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.domain.exceptions import ModelError, NotFoundError
from app.models import MlModel, ModelDeployment, ModelMonitor
from app.services import ml_service, quality_service


def _seed_training_data(session, tenant_id):
    """Seed customer analytical records with churn labels for training."""
    from app.models import AnalyticalRecord
    now = datetime.now(timezone.utc)
    for i in range(60):
        churned = 1 if i % 5 == 0 else 0
        session.add(AnalyticalRecord(
            tenant_id=tenant_id, contract="crm.customer.created.v1", entity_type="customer",
            entity_ref=f"c-{i}",
            normalized={"customer_id": f"c-{i}", "recent_payment_failures": (i % 4),
                        "support_ticket_count": (i % 3), "churned": churned,
                        "outage_exposure_count": (i % 2)},
            event_time=now - timedelta(days=(60 - i)), source="test"))
    session.commit()


def test_train_and_register_model(defaults, session, tenant_id):
    _seed_training_data(session, tenant_id)
    snap = quality_service.snapshot_dataset(session, tenant_id=tenant_id, code="ds-churn",
                                            contracts=["crm.customer.created.v1"])
    session.commit()
    result = ml_service.train_and_register(
        session, tenant_id=tenant_id, model_code="churn_model", snapshot_id=snap.id,
        config={"algorithm": "WEIGHTED_LOGIT",
                "feature_names": ["recent_payment_failures", "support_ticket_count"],
                "parameters": {"intercept": -0.5, "weights": {"recent_payment_failures": 0.8,
                                                              "support_ticket_count": 0.4}},
                "decision_threshold": 0.5, "use_case": "CHURN",
                "name": "Churn model", "feature_set_version": "v1"},
        owner="data-scientist")
    session.commit()
    assert result["version"] == 1
    assert "precision" in result["metrics"]
    model = ml_service.get_model(session, uuid.UUID(result["model_id"]))
    assert model.state == "EVALUATED"
    assert model.artifact_checksum  # artifact integrity


def test_approve_deploy_rollback(defaults, session, tenant_id):
    _seed_training_data(session, tenant_id)
    snap = quality_service.snapshot_dataset(session, tenant_id=tenant_id, code="ds-2",
                                            contracts=["crm.customer.created.v1"])
    session.commit()
    result = ml_service.train_and_register(session, tenant_id=tenant_id, model_code="churn_model",
                                           snapshot_id=snap.id,
                                           config={"algorithm": "WEIGHTED_LOGIT",
                                                   "feature_names": ["recent_payment_failures"],
                                                   "parameters": {"weights": {"recent_payment_failures": 0.8}},
                                                   "use_case": "CHURN"},
                                           owner="ds")
    session.commit()
    model = ml_service.get_model(session, uuid.UUID(result["model_id"]))
    ml_service.approve_model(session, model.id, approved_by="mlops")
    session.commit()
    assert model.approval_status == "APPROVED"
    # Deploy to SHADOW -> CANARY -> PRODUCTION.
    for env in ("SHADOW", "CANARY", "PRODUCTION"):
        ml_service.deploy_model(session, model.id, env, actor="mlops")
        session.commit()
        assert model.state == env
    ml_service.rollback_model(session, model.id, actor="mlops")
    session.commit()
    assert model.state == "ROLLED_BACK"
    # A subsequent version records the previous version as its rollback target.
    result2 = ml_service.train_and_register(session, tenant_id=tenant_id, model_code="churn_model",
                                            snapshot_id=snap.id,
                                            config={"algorithm": "WEIGHTED_LOGIT",
                                                    "feature_names": ["recent_payment_failures"],
                                                    "parameters": {"weights": {"recent_payment_failures": 0.8}},
                                                    "use_case": "CHURN"},
                                            owner="ds")
    session.commit()
    model2 = ml_service.get_model(session, uuid.UUID(result2["model_id"]))
    assert model2.version == 2
    assert model2.rollback_target == str(model.id)


def test_cannot_deploy_unapproved_model(defaults, session, tenant_id):
    _seed_training_data(session, tenant_id)
    snap = quality_service.snapshot_dataset(session, tenant_id=tenant_id, code="ds-3",
                                            contracts=["crm.customer.created.v1"])
    session.commit()
    result = ml_service.train_and_register(session, tenant_id=tenant_id, model_code="churn_model",
                                           snapshot_id=snap.id,
                                           config={"algorithm": "WEIGHTED_LOGIT",
                                                   "feature_names": [], "parameters": {},
                                                   "use_case": "CHURN"})
    session.commit()
    model = ml_service.get_model(session, uuid.UUID(result["model_id"]))
    with pytest.raises(ModelError):
        ml_service.deploy_model(session, model.id, "PRODUCTION", actor="x")


def test_duplicate_model_codes_versioned(defaults, session, tenant_id):
    _seed_training_data(session, tenant_id)
    snap = quality_service.snapshot_dataset(session, tenant_id=tenant_id, code="ds-4",
                                            contracts=["crm.customer.created.v1"])
    session.commit()
    for _ in range(2):
        result = ml_service.train_and_register(
            session, tenant_id=tenant_id, model_code="churn_model", snapshot_id=snap.id,
            config={"algorithm": "WEIGHTED_LOGIT", "feature_names": [], "parameters": {},
                    "use_case": "CHURN"})
        session.commit()
    models = session.query(MlModel).filter(MlModel.model_code == "churn_model").all()
    versions = sorted(m.version for m in models)
    assert versions == [1, 2]


def test_drift_detection_alert(defaults, session, tenant_id):
    _seed_training_data(session, tenant_id)
    snap = quality_service.snapshot_dataset(session, tenant_id=tenant_id, code="ds-5",
                                            contracts=["crm.customer.created.v1"])
    session.commit()
    result = ml_service.train_and_register(session, tenant_id=tenant_id, model_code="churn_model",
                                           snapshot_id=snap.id,
                                           config={"algorithm": "WEIGHTED_LOGIT",
                                                   "feature_names": [], "parameters": {},
                                                   "use_case": "CHURN"})
    session.commit()
    model = ml_service.get_model(session, uuid.UUID(result["model_id"]))
    monitor = ml_service.detect_drift(session, model.id, expected_mean=0.5, observed_mean=0.95,
                                      threshold=0.2)
    session.commit()
    assert monitor.alert is True
    assert monitor.metric_type == "prediction_drift"


def test_model_card_generated(defaults, session, tenant_id):
    from app.models import ModelCard
    _seed_training_data(session, tenant_id)
    snap = quality_service.snapshot_dataset(session, tenant_id=tenant_id, code="ds-6",
                                            contracts=["crm.customer.created.v1"])
    session.commit()
    result = ml_service.train_and_register(session, tenant_id=tenant_id, model_code="churn_model",
                                           snapshot_id=snap.id,
                                           config={"algorithm": "WEIGHTED_LOGIT",
                                                   "feature_names": [], "parameters": {},
                                                   "use_case": "CHURN",
                                                   "purpose": "Churn risk 30d",
                                                   "known_limitations": ["small sample"]})
    session.commit()
    card = session.query(ModelCard).filter(ModelCard.model_id == uuid.UUID(result["model_id"])).first()
    assert card is not None
    assert "small sample" in card.known_limitations
