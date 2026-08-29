"""Field SLA policy management + orchestration: versioned immutable policies,
targets, activation, validation and work-order SLA lookup."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.exceptions import ValidationError
from ..domain.sla import engine as sla_engine
from ..enums import FIELD_SLA_TARGET_KINDS
from ..models import (
    FieldSLAInstance,
    FieldSLAPolicy,
    FieldSLAPolicyVersion,
    FieldSLATarget,
    WorkOrder,
)
from .audit_service import audit, correlation


def _now() -> datetime:
    return datetime.now(timezone.utc)


def validate_definition(definition: dict) -> None:
    if not isinstance(definition, dict):
        raise ValidationError("field SLA definition must be an object")
    for threshold in definition.get("escalation", []):
        if threshold.get("target") not in ("ARRIVAL", "TIME_TO_COMPLETE"):
            raise ValidationError("escalation target must be ARRIVAL or TIME_TO_COMPLETE")
        if not (0 < threshold.get("at_risk_pct", 0) < 100):
            raise ValidationError("escalation at_risk_pct must be between 0 and 100")


def create_policy(session: Session, tenant_id, *, code: str, name: str, actor: str | None = None) -> FieldSLAPolicy:
    existing = session.scalars(select(FieldSLAPolicy).where(FieldSLAPolicy.tenant_id == tenant_id,
                                                            FieldSLAPolicy.code == code)).first()
    if existing is not None:
        raise ValidationError(f"field SLA policy {code!r} already exists for tenant")
    policy = FieldSLAPolicy(tenant_id=tenant_id, code=code, name=name, is_active=False, current_version=0)
    session.add(policy)
    session.flush()
    audit(session, tenant_id, "workforce.field_sla.policy_created", "field_sla_policy", str(policy.id), actor=actor,
          correlation_id=correlation(None), safe_after={"code": code, "name": name})
    return policy


def create_version(session: Session, tenant_id, policy_id, *, definition: dict, targets: list[dict],
                   actor: str | None = None, activate: bool = False) -> FieldSLAPolicyVersion:
    policy = session.get(FieldSLAPolicy, policy_id)
    if policy is None or policy.tenant_id != tenant_id:
        raise ValidationError("field SLA policy not found")
    validate_definition(definition)
    version = FieldSLAPolicyVersion(
        tenant_id=tenant_id, policy_id=policy.id, version=policy.current_version + 1,
        is_active=False, definition=definition)
    session.add(version)
    session.flush()
    for target in targets:
        kind = (target.get("kind") or "").upper()
        if kind not in FIELD_SLA_TARGET_KINDS and kind not in ("ARRIVAL", "TIME_TO_COMPLETE"):
            raise ValidationError(f"invalid field SLA target kind {kind!r}")
        seconds = int(target.get("business_seconds", 0))
        if seconds <= 0:
            raise ValidationError("field SLA target business_seconds must be positive")
        session.add(FieldSLATarget(tenant_id=tenant_id, version_id=version.id,
                                   priority=(target.get("priority") or "ALL").upper(),
                                   kind=kind, business_seconds=seconds))
    if activate:
        _deactivate_others(session, policy)
        version.is_active = True
        version.activated_at = _now()
        version.activated_by = actor
        policy.current_version = version.version
        policy.is_active = True
    session.flush()
    audit(session, tenant_id, "workforce.field_sla.version_created", "field_sla_policy_version", str(version.id),
          actor=actor, correlation_id=correlation(None),
          safe_after={"policy": policy.code, "version": version.version, "active": activate})
    return version


def _deactivate_others(session: Session, policy: FieldSLAPolicy) -> None:
    for other in session.scalars(select(FieldSLAPolicyVersion).where(
            FieldSLAPolicyVersion.policy_id == policy.id, FieldSLAPolicyVersion.is_active.is_(True))):
        other.is_active = False


def activate_version(session: Session, tenant_id, policy_id, version: int, actor: str | None = None) -> FieldSLAPolicyVersion:
    policy = session.get(FieldSLAPolicy, policy_id)
    if policy is None or policy.tenant_id != tenant_id:
        raise ValidationError("field SLA policy not found")
    version_row = session.scalars(select(FieldSLAPolicyVersion).where(
        FieldSLAPolicyVersion.policy_id == policy_id, FieldSLAPolicyVersion.version == version)).first()
    if version_row is None:
        raise ValidationError(f"field SLA policy version {version} not found")
    _deactivate_others(session, policy)
    version_row.is_active = True
    version_row.activated_at = _now()
    version_row.activated_by = actor
    policy.current_version = version
    policy.is_active = True
    session.flush()
    return version_row


def select_policy(session: Session, tenant_id, *, work_order_type: str, priority: str) -> tuple[FieldSLAPolicy, str]:
    return sla_engine.select_policy(session, tenant_id, work_order_type=work_order_type, priority=priority)


def active_version(session: Session, policy: FieldSLAPolicy) -> FieldSLAPolicyVersion:
    return sla_engine.active_version(session, policy)


def instantiate_field_sla(session: Session, work_order: WorkOrder, policy: FieldSLAPolicy,
                          version: FieldSLAPolicyVersion, calendar, *, selected_reason: str, priority: str):
    return sla_engine.instantiate_field_sla(session, work_order, policy, version, calendar,
                                            selected_reason=selected_reason, priority=priority)


def get_field_sla(session: Session, work_order: WorkOrder) -> FieldSLAInstance | None:
    return session.scalars(select(FieldSLAInstance).where(FieldSLAInstance.work_order_id == work_order.id)).first()


def sla_timeline(session: Session, sla: FieldSLAInstance) -> dict:
    from ..models import FieldSLAPause, WorkOrderEvent

    pauses = list(session.scalars(select(FieldSLAPause).where(FieldSLAPause.sla_id == sla.id)
                                  .order_by(FieldSLAPause.paused_at)))
    events = list(session.scalars(select(WorkOrderEvent).where(WorkOrderEvent.work_order_id == sla.work_order_id)
                                  .order_by(WorkOrderEvent.aggregate_version)))
    return {
        "sla_id": str(sla.id), "status": sla.status, "policy_id": str(sla.policy_id),
        "policy_version": sla.policy_version, "timezone": sla.timezone,
        "arrival_deadline": sla.arrival_deadline.isoformat(), "completion_deadline": sla.completion_deadline.isoformat(),
        "at_risk_at": sla.at_risk_at.isoformat() if sla.at_risk_at else None,
        "breach_at": sla.breach_at.isoformat() if sla.breach_at else None,
        "paused_accumulated_seconds": sla.paused_accumulated_seconds,
        "pauses": [{"paused_at": p.paused_at.isoformat(), "resumed_at": p.resumed_at.isoformat() if p.resumed_at else None,
                    "reason": p.reason, "policy_rule": p.policy_rule} for p in pauses],
        "events": [{"version": e.aggregate_version, "type": e.event_type} for e in events
                   if e.event_type.startswith("work_order.sla_")],
    }


def apply_exception(session: Session, tenant_id, work_order: WorkOrder, *, arrival_deadline, completion_deadline,
                    reason: str, actor: str) -> FieldSLAInstance:
    """Authorized audited SLA exception override."""
    sla = get_field_sla(session, work_order)
    if sla is None:
        raise ValidationError("work order has no field SLA instance")
    sla.arrival_deadline = arrival_deadline
    sla.completion_deadline = completion_deadline
    sla.selected_reason = f"EXCEPTION: {reason}"
    sla.status = "ACTIVE"
    audit(session, tenant_id, "workforce.field_sla.exception", "work_order", str(work_order.id), actor=actor,
          reason=reason, correlation_id=correlation(None),
          safe_after={"arrival_deadline": arrival_deadline.isoformat(), "completion_deadline": completion_deadline.isoformat()})
    session.flush()
    return sla
