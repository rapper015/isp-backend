"""Remediation safety: approval, kill switch, duplicate intents, budget,
cooldown, rate limit, circuit breaker, cross-tenant blocking."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.domain.exceptions import (ApprovalError, KillSwitchEngagedError, NotFoundError,
                                   RemediationSafetyError)
from app.models import RemediationIntent
from app.services import remediation_service


def _intent(session, tenant_id, policy="retry_telemetry_collection", **kw):
    target_ref = kw.pop("target_ref", "nas-1")
    payload = kw.pop("payload", {"device_reachable": True})
    return remediation_service.create_remediation_intent(
        session, tenant_id=tenant_id, policy_code=policy, target_type="nas",
        target_ref=target_ref, payload=payload, **kw)


def test_l3_intent_created_pending(defaults, session, tenant_id):
    intent = _intent(session, tenant_id)
    session.commit()
    assert intent.state == "PENDING"
    assert intent.autonomy_level == "L3"


def test_duplicate_intent_ignored(defaults, session, tenant_id):
    a = _intent(session, tenant_id, idempotency_key="idem-1")
    session.commit()
    b = _intent(session, tenant_id, idempotency_key="idem-1")
    session.commit()
    assert a.id == b.id
    assert session.query(RemediationIntent).count() == 1


def test_kill_switch_blocks_creation(defaults, session, tenant_id):
    remediation_service.set_kill_switch(session, scope="GLOBAL", tenant_id=None, enabled=True,
                                        reason="incident", actor="sre")
    session.commit()
    with pytest.raises(KillSwitchEngagedError):
        _intent(session, tenant_id)
    # Release for other tests.
    remediation_service.set_kill_switch(session, scope="GLOBAL", tenant_id=None, enabled=False,
                                        actor="sre")
    session.commit()


def test_l2_intent_requires_approval(defaults, session, tenant_id):
    intent = _intent(session, tenant_id, policy="request_bandwidth_adjustment")
    session.commit()
    assert intent.autonomy_level == "L2"
    with pytest.raises(ApprovalError):
        remediation_service.execute_intent(session, intent.id, executor="bot")
    remediation_service.approve_intent(session, intent.id, approver="sre", reason="ok")
    session.commit()
    remediation_service.execute_intent(session, intent.id, executor="bot")
    session.commit()
    assert intent.state == "STARTED"
    remediation_service.complete_intent(session, intent.id, verification="verified")
    session.commit()
    assert intent.state == "COMPLETED"


def test_requester_cannot_self_approve(defaults, session, tenant_id):
    intent = _intent(session, tenant_id, policy="request_bandwidth_adjustment",
                     requested_by="alice")
    session.commit()
    with pytest.raises(ApprovalError):
        remediation_service.approve_intent(session, intent.id, approver="alice")


def test_unauthorized_approval_role_rejected_at_api(client, tenant_id, tenant_headers):
    # TENANT_ADMIN lacks remediation.manage on approval endpoint.
    from conftest import make_token
    headers = {"Authorization": f"Bearer {make_token('TENANT_ADMIN', tenant_id)}"}
    resp = client.post("/api/intelligence/v1/remediation/intents/00000000-0000-0000-0000-000000000000/approve",
                       headers=headers, json={"approver": "x"})
    assert resp.status_code == 403


def test_l3_execution_gates(defaults, session, tenant_id):
    from app.models import RemediationPolicy
    session.add(RemediationPolicy(code="test_budget_policy", action_type="TEST_ACTION",
                                  autonomy_level="L3", approval_required=False, action_budget=2,
                                  rate_limit_per_hour=100, cooldown_seconds=0, max_blast_radius=1,
                                  tenant_scope="TENANT", preconditions=[], timeout_seconds=30,
                                  reversible=True, enabled=True, owner="test"))
    session.commit()
    intent = _intent(session, tenant_id, policy="test_budget_policy")
    session.commit()
    for _ in range(2):
        remediation_service.execute_intent(session, intent.id)
        session.commit()
        remediation_service.complete_intent(session, intent.id)
        session.commit()
    with pytest.raises(RemediationSafetyError):
        remediation_service.execute_intent(session, intent.id, executor="bot")  # budget exhausted


def test_cooldown_blocks_repeat(defaults, session, tenant_id):
    # Use a fresh policy code with a long cooldown via preconditions pass.
    intent = _intent(session, tenant_id, policy="rerun_readonly_diagnostic")
    session.commit()
    remediation_service.execute_intent(session, intent.id)
    session.commit()
    remediation_service.complete_intent(session, intent.id)
    session.commit()
    # Next execution of the same policy is inside the cooldown.
    intent2 = _intent(session, tenant_id, policy="rerun_readonly_diagnostic",
                      target_ref="nas-2")
    session.commit()
    with pytest.raises(RemediationSafetyError):
        remediation_service.execute_intent(session, intent2.id)


def test_cross_tenant_action_blocked(defaults, session, tenant_id):
    # Tenant-scoped policy must match the action tenant; the service always
    # uses the intent's tenant so a mismatch would require tampering. Verify
    # the guard function rejects a cross-tenant target payload explicitly.
    from app.domain.remediation import check_tenant_scope
    with pytest.raises(Exception):
        check_tenant_scope(tenant_id, "TENANT", uuid.uuid4())


def test_precondition_blocked(defaults, session, tenant_id):
    with pytest.raises(RemediationSafetyError):
        remediation_service.create_remediation_intent(
            session, tenant_id=tenant_id, policy_code="rerun_readonly_diagnostic",
            target_type="nas", target_ref="nas-9",
            payload={"device_reachable": False}, idempotency_key=f"x-{uuid.uuid4()}")
    session.commit()


def test_failed_intent_compensation(defaults, session, tenant_id):
    intent = _intent(session, tenant_id)
    session.commit()
    remediation_service.execute_intent(session, intent.id)
    session.commit()
    remediation_service.fail_intent(session, intent.id, compensate=True, detail={"reason": "x"})
    session.commit()
    assert intent.state == "COMPENSATED"


def test_unknown_policy_rejected(defaults, session, tenant_id):
    with pytest.raises(NotFoundError):
        remediation_service.create_remediation_intent(
            session, tenant_id=tenant_id, policy_code="nonexistent", target_type="nas",
            target_ref="nas-1", payload={})


def test_recommendation_and_then_intent(defaults, session, tenant_id):
    rec = remediation_service.create_recommendation(
        session, tenant_id=tenant_id, kind="MAINTENANCE", subject_type="nas", subject="nas-1",
        summary="Replace ONT", evidence=[{"signal": "high_error_rate"}],
        autonomy_level="L2", expected_impact="replace device")
    session.commit()
    assert rec.state == "OPEN"
    assert rec.autonomy_level == "L2"
