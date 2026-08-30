"""Churn + retention candidate flow. Scores never issue discounts."""
from datetime import datetime, timedelta, timezone

from app.models import ChurnScore, MlModel
from app.services import churn_service


def _seed_customer(session, tenant_id, customer, failures=0, tickets=0):
    from app.models import AnalyticalRecord
    now = datetime.now(timezone.utc)
    session.add(AnalyticalRecord(tenant_id=tenant_id, contract="crm.customer.created.v1",
                                 entity_type="customer", entity_ref=customer,
                                 normalized={"customer_id": customer, "recent_payment_failures": failures,
                                             "support_ticket_count": tickets, "tenure_days": 120},
                                 event_time=now - timedelta(days=30), source="test"))
    session.commit()


def test_churn_score_uses_production_model(defaults, session, tenant_id):
    _seed_customer(session, tenant_id, "c-1", failures=0)
    # baseline churn model is pre-seeded as PRODUCTION.
    row = churn_service.score_customer(session, tenant_id=tenant_id, customer_ref="c-1",
                                       horizon_days=30)
    session.commit()
    assert 0.0 <= row.score <= 1.0
    assert row.risk_band in ("LOW", "MEDIUM", "HIGH", "CRITICAL")
    assert row.state == "ACTIVE"
    assert row.recommended_action


def test_churn_score_drivers(defaults, session, tenant_id):
    _seed_customer(session, tenant_id, "c-risk", failures=4, tickets=3)
    row = churn_service.score_customer(session, tenant_id=tenant_id, customer_ref="c-risk",
                                       horizon_days=30)
    session.commit()
    assert len(row.top_drivers) > 0
    assert row.model_version == 1


def test_retention_candidate_created_and_tracked(defaults, session, tenant_id):
    _seed_customer(session, tenant_id, "c-2")
    score = churn_service.score_customer(session, tenant_id=tenant_id, customer_ref="c-2")
    session.commit()
    candidate = churn_service.create_retention_candidate(session, score.id)
    session.commit()
    assert candidate.offer_presented is False
    churn_service.track_offer(session, candidate.id, presented=True, consent=True,
                              accepted=True, outcome="RETAINED", experiment_id="exp-1")
    session.commit()
    assert candidate.offer_presented is True
    assert candidate.offer_accepted is True
    assert candidate.experiment_id == "exp-1"


def test_expire_scores(defaults, session, tenant_id):
    _seed_customer(session, tenant_id, "c-old")
    score = churn_service.score_customer(session, tenant_id=tenant_id, customer_ref="c-old",
                                         horizon_days=30)
    session.commit()
    # Force expiry.
    score.expiry_at = datetime.now(timezone.utc) - timedelta(days=1)
    session.commit()
    count = churn_service.expire_scores(session, tenant_id)
    session.commit()
    assert count == 1
    assert score.state == "EXPIRED"


def test_score_records_model_version_and_ts(defaults, session, tenant_id):
    _seed_customer(session, tenant_id, "c-v")
    score = churn_service.score_customer(session, tenant_id=tenant_id, customer_ref="c-v")
    session.commit()
    assert score.model_code == "churn_baseline_30d"
    assert score.feature_timestamp is not None
    assert score.expiry_at > score.feature_timestamp
