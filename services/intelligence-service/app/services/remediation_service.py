"""Recommendations + remediation intents with safety controls.

The intelligence layer NEVER mutates domain state directly. Operational
changes are expressed as remediation intents that pass policy evaluation,
approval and the authoritative service's own saga/workflow."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..domain.exceptions import (ApprovalError, CrossTenantActionError, KillSwitchEngagedError,
                                 NotFoundError, RemediationSafetyError)
from ..domain.remediation import (autonomy_for_action, check_blast_radius, check_budget,
                                  check_circuit_breaker, check_cooldown, check_kill_switch,
                                  check_preconditions, check_rate_limit, check_tenant_scope,
                                  is_executable, requires_approval, verify_approval)
from ..models import (KillSwitch, Recommendation, RemediationApproval, RemediationIntent,
                      RemediationOutcome, RemediationPolicy, RemediationStep)
from ..state_machine import guarded as intent_guarded
from .audit_service import audit, outbox


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid(value):
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


def get_kill_switches(session: Session, tenant_id) -> tuple[bool, bool]:
    global_switch = session.scalars(select(KillSwitch).where(
        KillSwitch.scope == "GLOBAL", KillSwitch.enabled.is_(True))).first()
    tenant_switch = session.scalars(select(KillSwitch).where(
        KillSwitch.scope == "TENANT", KillSwitch.tenant_id == tenant_id,
        KillSwitch.enabled.is_(True))).first()
    return bool(global_switch), bool(tenant_switch)


def set_kill_switch(session: Session, *, scope: str, tenant_id, enabled: bool,
                    reason: str | None = None, actor: str | None = None) -> KillSwitch:
    row = session.scalars(select(KillSwitch).where(
        KillSwitch.scope == scope, KillSwitch.tenant_id == tenant_id)).first()
    if row is None:
        row = KillSwitch(scope=scope, tenant_id=tenant_id, enabled=enabled, reason=reason,
                         set_by=actor, set_at=_now())
        session.add(row)
    else:
        row.enabled = enabled
        row.reason = reason
        row.set_by = actor
        row.set_at = _now()
    session.flush()
    audit(session, tenant_id, actor, f"kill_switch.{'engage' if enabled else 'release'}",
          resource_type="kill_switch", resource_id=row.id, after={"scope": scope})
    if enabled:
        outbox(session, "ai.kill_switch_engaged.v1", tenant_id, None,
               {"scope": scope, "tenant_id": str(tenant_id) if tenant_id else None},
               idempotency_key=f"kill-switch:{scope}:{tenant_id}")
    return row


def create_recommendation(session: Session, *, tenant_id, kind: str, subject_type: str, subject: str,
                          summary: str, evidence: list, autonomy_level: str = "L1",
                          model_code: str | None = None, model_version: int | None = None,
                          confidence: float = 0.0, expected_impact: str | None = None,
                          expires_hours: int = 72, correlation_id: str | None = None) -> Recommendation:
    row = Recommendation(tenant_id=tenant_id, kind=kind, subject_type=subject_type, subject=subject,
                         summary=summary, evidence=evidence, autonomy_level=autonomy_level,
                         model_code=model_code, model_version=model_version, state="OPEN",
                         expires_at=_now() + timedelta(hours=expires_hours), confidence=confidence,
                         expected_impact=expected_impact, correlation_id=correlation_id)
    session.add(row)
    session.flush()
    outbox(session, "ai.recommendation_created.v1", tenant_id, correlation_id,
           {"recommendation_id": str(row.id), "kind": kind, "subject": subject,
            "autonomy_level": autonomy_level},
           idempotency_key=f"recommendation:{row.id}")
    return row


def create_remediation_intent(session: Session, *, tenant_id, policy_code: str, target_type: str,
                              target_ref: str, payload: dict, correlation_id: str | None = None,
                              causation_id: str | None = None, idempotency_key: str | None = None,
                              requested_by: str | None = None) -> RemediationIntent:
    # Kill switch + policy resolution first.
    global_on, tenant_on = get_kill_switches(session, tenant_id)
    check_kill_switch(global_on, tenant_on)
    policy = session.scalars(select(RemediationPolicy).where(
        RemediationPolicy.code == policy_code)).first()
    if policy is None:
        raise NotFoundError(f"remediation policy {policy_code} not found")
    if not policy.enabled:
        raise RemediationSafetyError("remediation policy disabled")
    check_tenant_scope(tenant_id, policy.tenant_scope, tenant_id)
    level = autonomy_for_action(policy.action_type, policy.autonomy_level)
    key = idempotency_key or f"{tenant_id}:{policy_code}:{target_type}:{target_ref}"
    existing = session.scalars(select(RemediationIntent).where(
        RemediationIntent.idempotency_key == key)).first()
    if existing is not None:
        return existing  # duplicate intent ignored
    intent = RemediationIntent(
        tenant_id=tenant_id, policy_code=policy_code, action_type=policy.action_type,
        target_type=target_type, target_ref=target_ref, autonomy_level=level,
        state="PENDING", correlation_id=correlation_id, causation_id=causation_id,
        idempotency_key=key, payload=payload, budget_used=0, attempt=0,
        requested_by=requested_by, requested_at=_now())
    session.add(intent)
    session.flush()
    # Preconditions (fail safe at creation for L3; L2 evaluated before execution).
    try:
        check_preconditions(payload, policy.preconditions or [])
    except RemediationSafetyError:
        intent.state = "BLOCKED"
        session.flush()
        raise
    if requires_approval(level):
        outbox(session, "ai.remediation_requested.v1", tenant_id, correlation_id,
               {"intent_id": str(intent.id), "action_type": intent.action_type,
                "target_ref": target_ref, "autonomy_level": level},
               idempotency_key=f"remediation-requested:{intent.id}")
    audit(session, tenant_id, requested_by, "remediation.requested", resource_type="remediation",
          resource_id=intent.id, after={"policy": policy_code, "level": level})
    session.flush()
    return intent


def approve_intent(session: Session, intent_id: uuid.UUID, *, approver: str,
                   reason: str | None = None) -> RemediationIntent:
    intent = _get_intent(session, intent_id)
    if intent.state != "PENDING":
        raise ApprovalError(f"cannot approve intent in state {intent.state}")
    if not requires_approval(intent.autonomy_level):
        raise ApprovalError(f"approval not required for level {intent.autonomy_level}")
    # Prevent self-approval of own request.
    if intent.requested_by and intent.requested_by == approver:
        raise ApprovalError("requester cannot approve their own remediation intent")
    session.add(RemediationApproval(tenant_id=intent.tenant_id, intent_id=intent.id,
                                    approver=approver, decision="APPROVED", reason=reason,
                                    decided_at=_now()))
    intent.state = "APPROVED"
    session.flush()
    outbox(session, "ai.remediation_approved.v1", intent.tenant_id, intent.correlation_id,
           {"intent_id": str(intent.id)}, idempotency_key=f"remediation-approved:{intent.id}")
    audit(session, intent.tenant_id, approver, "remediation.approved", resource_type="remediation",
          resource_id=intent.id)
    return intent


def reject_intent(session: Session, intent_id: uuid.UUID, *, approver: str,
                  reason: str | None = None) -> RemediationIntent:
    intent = _get_intent(session, intent_id)
    if intent.state != "PENDING":
        raise ApprovalError(f"cannot reject intent in state {intent.state}")
    session.add(RemediationApproval(tenant_id=intent.tenant_id, intent_id=intent.id,
                                    approver=approver, decision="REJECTED", reason=reason,
                                    decided_at=_now()))
    intent.state = "REJECTED"
    session.flush()
    outbox(session, "ai.remediation_rejected.v1", intent.tenant_id, intent.correlation_id,
           {"intent_id": str(intent.id)}, idempotency_key=f"remediation-rejected:{intent.id}")
    audit(session, intent.tenant_id, approver, "remediation.rejected", resource_type="remediation",
          resource_id=intent.id)
    return intent


def execute_intent(session: Session, intent_id: uuid.UUID, *, executor: str | None = None,
                   record: dict | None = None) -> RemediationIntent:
    intent = _get_intent(session, intent_id)
    if not is_executable(intent.autonomy_level):
        raise RemediationSafetyError(f"autonomy level {intent.autonomy_level} is not executable")
    if intent.state != "APPROVED" and requires_approval(intent.autonomy_level):
        raise ApprovalError("approved intent required before execution")
    # Safety gates.
    global_on, tenant_on = get_kill_switches(session, intent.tenant_id)
    check_kill_switch(global_on, tenant_on)
    policy = session.scalars(select(RemediationPolicy).where(
        RemediationPolicy.code == intent.policy_code)).first()
    if policy is None or not policy.enabled:
        raise RemediationSafetyError("remediation policy unavailable")
    check_budget(intent.budget_used, policy.action_budget)
    check_rate_limit(_hourly_executions(session, intent.policy_code), policy.rate_limit_per_hour)
    last = _last_execution(session, intent.policy_code)
    last_ts = last.timestamp() if last is not None else None
    if last is not None and last.tzinfo is None:
        last_ts = last.replace(tzinfo=timezone.utc).timestamp()
    check_cooldown(last_ts, policy.cooldown_seconds, _now().timestamp())
    check_circuit_breaker(_recent_failures(session, intent.policy_code), policy.circuit_breaker.get("threshold", 3))
    check_blast_radius(1, policy.max_blast_radius)
    check_preconditions(record or intent.payload, policy.preconditions or [])

    intent.state = "STARTED"
    intent.attempt += 1
    intent.executed_at = _now()
    session.add(RemediationStep(tenant_id=intent.tenant_id, intent_id=intent.id,
                                step="EXECUTE", state="RUNNING", detail={"executor": executor},
                                started_at=_now()))
    session.flush()
    outbox(session, "ai.remediation_started.v1", intent.tenant_id, intent.correlation_id,
           {"intent_id": str(intent.id)}, idempotency_key=f"remediation-started:{intent.id}")
    return intent


def complete_intent(session: Session, intent_id: uuid.UUID, *, result: str = "SUCCESS",
                    verification: str | None = None, detail: dict | None = None) -> RemediationIntent:
    intent = _get_intent(session, intent_id)
    if intent.state != "STARTED":
        raise RemediationSafetyError(f"cannot complete intent in state {intent.state}")
    intent.state = "COMPLETED"
    intent.budget_used += 1
    session.add(RemediationOutcome(tenant_id=intent.tenant_id, intent_id=intent.id, result=result,
                                   verification=verification, rollback_performed=False,
                                   detail=detail or {}, occurred_at=_now()))
    session.flush()
    outbox(session, "ai.remediation_completed.v1", intent.tenant_id, intent.correlation_id,
           {"intent_id": str(intent.id), "result": result}, idempotency_key=f"remediation-completed:{intent.id}")
    return intent


def fail_intent(session: Session, intent_id: uuid.UUID, *, detail: dict | None = None,
                compensate: bool = False) -> RemediationIntent:
    intent = _get_intent(session, intent_id)
    if intent.state != "STARTED":
        raise RemediationSafetyError(f"cannot fail intent in state {intent.state}")
    session.add(RemediationOutcome(tenant_id=intent.tenant_id, intent_id=intent.id, result="FAILURE",
                                   verification=None, rollback_performed=compensate,
                                   detail=detail or {}, occurred_at=_now()))
    if compensate:
        intent.state = "COMPENSATED"
        outbox(session, "ai.remediation_compensated.v1", intent.tenant_id, intent.correlation_id,
               {"intent_id": str(intent.id)}, idempotency_key=f"remediation-compensated:{intent.id}")
    else:
        intent.state = "FAILED"
        outbox(session, "ai.remediation_failed.v1", intent.tenant_id, intent.correlation_id,
               {"intent_id": str(intent.id)}, idempotency_key=f"remediation-failed:{intent.id}")
    session.flush()
    return intent


def _get_intent(session: Session, intent_id: uuid.UUID) -> RemediationIntent:
    intent = session.get(RemediationIntent, intent_id)
    if intent is None:
        raise NotFoundError("remediation intent not found")
    return intent


def _hourly_executions(session: Session, policy_code: str) -> int:
    since = _now() - timedelta(hours=1)
    return session.scalar(select(func.count(RemediationIntent.id)).where(
        RemediationIntent.policy_code == policy_code,
        RemediationIntent.state.in_(("COMPLETED", "STARTED")),
        RemediationIntent.requested_at >= since)) or 0


def _last_execution(session: Session, policy_code: str) -> datetime | None:
    row = session.execute(select(RemediationIntent.executed_at).where(
        RemediationIntent.policy_code == policy_code,
        RemediationIntent.executed_at.isnot(None)).order_by(RemediationIntent.executed_at.desc())
        .limit(1)).scalars().first()
    return row


def _recent_failures(session: Session, policy_code: str) -> int:
    since = _now() - timedelta(hours=1)
    return session.scalar(select(func.count(RemediationIntent.id)).where(
        RemediationIntent.policy_code == policy_code,
        RemediationIntent.state == "FAILED", RemediationIntent.requested_at >= since)) or 0
