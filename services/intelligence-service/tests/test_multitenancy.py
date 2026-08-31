"""Multi-tenant isolation: fail-closed context, cross-tenant rejection,
platform aggregates."""
import uuid

import pytest

from app.context import TenantContext, reset_context, set_context
from app.domain.exceptions import TenantContextRequiredError, TenantIsolationError
from app.models import ChurnScore, FraudCase
from app.routing import enforce_scope, require_platform_aggregate
from app.services import fraud_service


def _ctx(tenant_id):
    return TenantContext(tenant_id=tenant_id, user_id="u", role="TENANT_ADMIN",
                         permissions=frozenset({"fraud.view"}))


def test_enforce_scope_fails_closed_without_context():
    with pytest.raises(TenantContextRequiredError):
        enforce_scope(uuid.uuid4())


def test_enforce_scope_mismatch_rejected():
    token = set_context(_ctx(uuid.uuid4()))
    try:
        with pytest.raises(TenantIsolationError):
            enforce_scope(uuid.uuid4())
    finally:
        reset_context(token)


def test_platform_aggregate_requires_explicit_scope():
    tid = uuid.uuid4()
    token = set_context(_ctx(tid))
    try:
        with pytest.raises(Exception):
            require_platform_aggregate()
    finally:
        reset_context(token)


def test_fraud_tenant_isolation(defaults, session):
    a = uuid.uuid4()
    b = uuid.uuid4()
    for tenant, subj in ((a, "sub-a"), (b, "sub-b")):
        fraud_service.evaluate_rules(session, tenant_id=tenant, subject_type="subscriber",
                                     subject=subj, record={"auth_failure_rate": 0.95})
    session.commit()
    rows_a = session.query(FraudCase).filter(FraudCase.tenant_id == a).all()
    rows_b = session.query(FraudCase).filter(FraudCase.tenant_id == b).all()
    # No cases yet (cases are opened explicitly), but signals are isolated.
    from app.models import FraudSignal
    sigs_a = {s.subject for s in session.query(FraudSignal).filter(FraudSignal.tenant_id == a).all()}
    sigs_b = {s.subject for s in session.query(FraudSignal).filter(FraudSignal.tenant_id == b).all()}
    assert sigs_a == {"sub-a"}
    assert sigs_b == {"sub-b"}
    assert sigs_a.isdisjoint(sigs_b)


def test_churn_tenant_isolation(defaults, session):
    from app.models import AnalyticalRecord
    a = uuid.uuid4()
    b = uuid.uuid4()
    now = __import__("datetime", fromlist=["datetime"]).datetime.now(__import__("datetime", fromlist=["timezone"]).timezone.utc)
    for tenant, cust in ((a, "ca"), (b, "cb")):
        session.add(AnalyticalRecord(tenant_id=tenant, contract="crm.customer.created.v1",
                                     entity_type="customer", entity_ref=cust,
                                     normalized={"customer_id": cust, "recent_payment_failures": 0},
                                     event_time=now))
    session.commit()
    from app.services.churn_service import score_customer
    score_customer(session, tenant_id=a, customer_ref="ca")
    score_customer(session, tenant_id=b, customer_ref="cb")
    session.commit()
    scores_a = {s.customer_ref for s in session.query(ChurnScore).filter(ChurnScore.tenant_id == a).all()}
    scores_b = {s.customer_ref for s in session.query(ChurnScore).filter(ChurnScore.tenant_id == b).all()}
    assert scores_a == {"ca"}
    assert scores_b == {"cb"}
    assert scores_a.isdisjoint(scores_b)


def test_mgmt_auth_requires_token(client):
    resp = client.get("/api/intelligence/v1/models")
    assert resp.status_code == 401


def test_platform_aggregate_requires_platform_scope(client, tenant_id, tenant_headers):
    resp = client.get("/api/intelligence/v1/reports/executive", headers=tenant_headers)
    assert resp.status_code in (403, 422)


def test_platform_aggregate_allowed(client, platform_headers):
    resp = client.get("/api/intelligence/v1/reports/executive", headers=platform_headers)
    assert resp.status_code == 200
    assert resp.json()["is_platform_aggregate"] is True


def test_cross_tenant_model_list_isolated(defaults, session):
    a = uuid.uuid4()
    b = uuid.uuid4()
    from app.models import MlModel
    session.add(MlModel(tenant_id=a, model_code="m_a", version=1, use_case="CHURN",
                        algorithm="WEIGHTED_LOGIT", state="DRAFT"))
    session.add(MlModel(tenant_id=b, model_code="m_b", version=1, use_case="CHURN",
                        algorithm="WEIGHTED_LOGIT", state="DRAFT"))
    session.commit()
    models_a = [m.model_code for m in session.query(MlModel).filter(MlModel.tenant_id == a).all()]
    assert models_a == ["m_a"]
