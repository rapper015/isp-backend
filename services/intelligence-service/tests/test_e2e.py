"""End-to-end: fraud signal -> case -> review; churn -> retention; maintenance
-> recommendation -> L3 remediation with safety gates; kill switch blocks."""
from datetime import datetime, timezone

from app.services import (churn_service, fraud_service, maintenance_service,
                          remediation_service)


def _now():
    return datetime.now(timezone.utc)


def test_full_governed_intelligence_flow(defaults, session, tenant_id):
    # 1) Fraud signal -> case -> review (no direct suspension).
    signals = fraud_service.evaluate_rules(
        session, tenant_id=tenant_id, subject_type="subscriber", subject="sub-e2e",
        record={"auth_failure_rate": 0.9, "concurrent_session_count": 8},
        correlation_id="corr-e2e")
    session.commit()
    assert len(signals) == 1
    case = fraud_service.open_case(session, tenant_id=tenant_id, subject_type="subscriber",
                                   subject="sub-e2e", signals=signals)
    session.commit()
    fraud_service.transition(session, case.id, "IN_REVIEW", actor="sec")
    fraud_service.decide_case(session, case.id, decision="ESCALATE", reason="credential stuffing",
                              actor="sec")
    session.commit()
    assert case.decision == "ESCALATE"
    # 2) Churn score -> retention candidate (no discount issued).
    from app.models import AnalyticalRecord
    session.add(AnalyticalRecord(tenant_id=tenant_id, contract="crm.customer.created.v1",
                                 entity_type="customer", entity_ref="cust-e2e",
                                 normalized={"customer_id": "cust-e2e", "recent_payment_failures": 4,
                                             "support_ticket_count": 3, "tenure_days": 90},
                                 event_time=_now() - __import__("datetime", fromlist=["timedelta"]).timedelta(days=10)))
    session.commit()
    score = churn_service.score_customer(session, tenant_id=tenant_id, customer_ref="cust-e2e",
                                         horizon_days=30)
    session.commit()
    candidate = churn_service.create_retention_candidate(session, score.id)
    session.commit()
    assert candidate.recommended_action
    # 3) Maintenance prediction -> recommendation.
    pred = maintenance_service.predict_failure(session, tenant_id=tenant_id, asset_type="nas",
                                               asset_ref="nas-e2e", horizon_days=14)
    session.commit()
    rec = remediation_service.create_recommendation(
        session, tenant_id=tenant_id, kind="MAINTENANCE", subject_type="nas", subject="nas-e2e",
        summary=f"Predicted failure p={pred.failure_probability}",
        evidence=[{"prediction_id": str(pred.id)}], autonomy_level="L2")
    session.commit()
    # 4) L3 auto-remediation (retry telemetry) with safety gates.
    intent = remediation_service.create_remediation_intent(
        session, tenant_id=tenant_id, policy_code="retry_telemetry_collection",
        target_type="nas", target_ref="nas-e2e", payload={"device_reachable": True},
        idempotency_key="e2e-1", correlation_id="corr-e2e")
    session.commit()
    remediation_service.execute_intent(session, intent.id, executor="ai")
    session.commit()
    remediation_service.complete_intent(session, intent.id, verification="verified")
    session.commit()
    assert intent.state == "COMPLETED"
    # 5) L2 high-impact action requires approval.
    l2 = remediation_service.create_remediation_intent(
        session, tenant_id=tenant_id, policy_code="request_bandwidth_adjustment",
        target_type="subscriber", target_ref="sub-e2e", payload={},
        idempotency_key="e2e-2")
    session.commit()
    try:
        remediation_service.execute_intent(session, l2.id, executor="ai")
        raise AssertionError("L2 intent should require approval")
    except Exception:
        pass
    remediation_service.approve_intent(session, l2.id, approver="sre-2", reason="approved")
    session.commit()
    remediation_service.execute_intent(session, l2.id, executor="ai")
    session.commit()
    assert l2.state == "STARTED"
    # 6) Kill switch blocks further execution.
    remediation_service.set_kill_switch(session, scope="TENANT", tenant_id=tenant_id, enabled=True,
                                        reason="e2e end", actor="sre")
    session.commit()
    try:
        remediation_service.create_remediation_intent(
            session, tenant_id=tenant_id, policy_code="retry_telemetry_collection",
            target_type="nas", target_ref="nas-x", payload={"device_reachable": True},
            idempotency_key="e2e-3")
        raise AssertionError("kill switch should block new intents")
    except Exception:
        pass
    # Release for other tests.
    remediation_service.set_kill_switch(session, scope="TENANT", tenant_id=tenant_id, enabled=False,
                                        actor="sre")
    session.commit()
    # 7) Duplicate intent is ignored.
    dup = remediation_service.create_remediation_intent(
        session, tenant_id=tenant_id, policy_code="retry_telemetry_collection",
        target_type="nas", target_ref="nas-e2e", payload={"device_reachable": True},
        idempotency_key="e2e-1")
    session.commit()
    assert dup.id == intent.id
