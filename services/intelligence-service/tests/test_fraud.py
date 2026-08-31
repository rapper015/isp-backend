"""Fraud: signal -> case -> review -> recommendation. Never auto-suspends."""
import pytest

from app.models import FraudCase, FraudSignal
from app.services import fraud_service


def test_rule_eval_creates_signal(defaults, session, tenant_id):
    signals = fraud_service.evaluate_rules(
        session, tenant_id=tenant_id, subject_type="subscriber", subject="sub-1",
        record={"auth_failure_rate": 0.95, "concurrent_session_count": 6},
        correlation_id="corr-f")
    session.commit()
    assert len(signals) == 1
    assert signals[0].risk_score > 0
    assert signals[0].state == "OPEN"


def test_no_hit_no_signal(defaults, session, tenant_id):
    signals = fraud_service.evaluate_rules(
        session, tenant_id=tenant_id, subject_type="subscriber", subject="sub-ok",
        record={"auth_failure_rate": 0.05, "concurrent_session_count": 1})
    session.commit()
    assert signals == []


def test_model_score_fusion(defaults, session, tenant_id):
    signals = fraud_service.evaluate_rules(
        session, tenant_id=tenant_id, subject_type="subscriber", subject="sub-m",
        record={}, model_score=0.9, model_code="fraud_baseline", model_version=1)
    session.commit()
    assert len(signals) == 1
    assert "model_score" in signals[0].factors[0]


def test_signal_to_case_with_evidence(defaults, session, tenant_id):
    signals = fraud_service.evaluate_rules(
        session, tenant_id=tenant_id, subject_type="subscriber", subject="sub-2",
        record={"recent_payment_failures": 5, "usage_vs_plan_ratio": 4.0})
    session.commit()
    case = fraud_service.open_case(session, tenant_id=tenant_id, subject_type="subscriber",
                                   subject="sub-2", signals=signals)
    session.commit()
    assert case.state == "OPEN"
    assert case.decision in ("ESCALATE", "REVIEW", "MONITOR")


def test_case_transition_and_decision(defaults, session, tenant_id):
    signals = fraud_service.evaluate_rules(
        session, tenant_id=tenant_id, subject_type="subscriber", subject="sub-3",
        record={"auth_failure_rate": 0.99})
    session.commit()
    case = fraud_service.open_case(session, tenant_id=tenant_id, subject_type="subscriber",
                                   subject="sub-3", signals=signals)
    session.commit()
    fraud_service.transition(session, case.id, "IN_REVIEW", actor="sec-1")
    fraud_service.decide_case(session, case.id, decision="ESCALATE", reason="credential stuffing",
                              actor="sec-1")
    session.commit()
    assert case.state == "IN_REVIEW"
    assert case.decision == "ESCALATE"
    assert case.final_outcome == "ESCALATE"


def test_invalid_case_transition_rejected(defaults, session, tenant_id):
    signals = fraud_service.evaluate_rules(
        session, tenant_id=tenant_id, subject_type="subscriber", subject="sub-4",
        record={"concurrent_session_count": 9})
    session.commit()
    case = fraud_service.open_case(session, tenant_id=tenant_id, subject_type="subscriber",
                                   subject="sub-4", signals=signals)
    session.commit()
    with pytest.raises(Exception):
        fraud_service.transition(session, case.id, "ACTIONED", actor="x")  # OPEN cannot go straight to ACTIONED


def test_fraud_recommendation_not_direct_action(defaults, session, tenant_id):
    signals = fraud_service.evaluate_rules(
        session, tenant_id=tenant_id, subject_type="subscriber", subject="sub-5",
        record={"auth_failure_rate": 0.85})
    session.commit()
    case = fraud_service.open_case(session, tenant_id=tenant_id, subject_type="subscriber",
                                   subject="sub-5", signals=signals)
    session.commit()
    rec = fraud_service.recommend_action(session, case.id, action_type="VERIFY_IDENTITY",
                                         target_service="crm-service", rationale="verify customer")
    session.commit()
    assert rec.action_type == "VERIFY_IDENTITY"
    # The recommendation is OPEN — no subscriber suspension happened.
    subscriber = session.query(FraudCase).filter(FraudCase.subject == "sub-5").first()
    assert subscriber.state != "ACTIONED"
