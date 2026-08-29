"""Field SLA selection, instantiation, timer behaviour and evaluation.

Authoritative field SLA state is stored in the database (FieldSLAInstance).
Redis may accelerate checks but never holds SLA truth. The engine is
idempotent, restart-safe and reconciliation-capable:

invariant:  deadline = deadline_after(started_at, target + paused_accumulated_seconds)

Rescheduling never silently resets the original SLA; customer-requested
reschedules and other policy-listed states pause the clock."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import (
    BusinessCalendar,
    FieldSLAInstance,
    FieldSLAPause,
    FieldSLAPolicy,
    FieldSLAPolicyVersion,
    FieldSLATarget,
    WorkOrder,
)
from ...services.audit_service import append_event, outbox
from .calendar import business_seconds_between, deadline_after, default_working_hours, load_holidays

PRIORITY_ALL = "ALL"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------
def select_policy(session: Session, tenant_id, *, work_order_type: str, priority: str) -> tuple[FieldSLAPolicy, str]:
    tenant_policies = list(session.scalars(
        select(FieldSLAPolicy).where(FieldSLAPolicy.tenant_id == tenant_id, FieldSLAPolicy.is_active.is_(True))
        .order_by(FieldSLAPolicy.created_at)))
    if tenant_policies:
        return tenant_policies[0], f"tenant:{tenant_policies[0].code}"
    global_policies = list(session.scalars(
        select(FieldSLAPolicy).where(FieldSLAPolicy.tenant_id.is_(None), FieldSLAPolicy.is_active.is_(True))
        .order_by(FieldSLAPolicy.created_at)))
    if global_policies:
        return global_policies[0], f"global:{global_policies[0].code}"
    raise ValueError("no active field SLA policy is configured")


def active_version(session: Session, policy: FieldSLAPolicy) -> FieldSLAPolicyVersion:
    version = session.scalars(
        select(FieldSLAPolicyVersion).where(FieldSLAPolicyVersion.policy_id == policy.id,
                                            FieldSLAPolicyVersion.is_active.is_(True))
    ).first()
    if version is None:
        raise ValueError(f"field SLA policy {policy.code!r} has no active version")
    return version


def resolve_targets(session: Session, version: FieldSLAPolicyVersion, priority: str) -> tuple[int, int]:
    """(arrival_seconds, completion_seconds)."""
    rows = list(session.scalars(select(FieldSLATarget).where(FieldSLATarget.version_id == version.id)))
    by = {(r.priority, r.kind): r.business_seconds for r in rows}

    def get(kind: str) -> int:
        if (priority, kind) in by:
            return by[(priority, kind)]
        if (PRIORITY_ALL, kind) in by:
            return by[(PRIORITY_ALL, kind)]
        return 4 * 3600 if kind == "ARRIVAL" else 8 * 3600

    return get("ARRIVAL"), get("TIME_TO_COMPLETE")


def sla_calendar_context(session: Session, calendar: BusinessCalendar):
    tz = _zoneinfo(calendar.timezone)
    holidays = load_holidays(session, calendar.id)
    working_hours = calendar.working_hours or default_working_hours()
    return tz, holidays, working_hours


def _zoneinfo(name: str):
    from zoneinfo import ZoneInfo

    try:
        return ZoneInfo(name or "UTC")
    except Exception:  # noqa: BLE001
        return ZoneInfo("UTC")


def instantiate_field_sla(
    session: Session,
    work_order: WorkOrder,
    policy: FieldSLAPolicy,
    version: FieldSLAPolicyVersion,
    calendar: BusinessCalendar,
    *,
    selected_reason: str,
    priority: str,
    effective_at: datetime | None = None,
) -> FieldSLAInstance:
    now = _aware(effective_at or _now())
    arrival_seconds, completion_seconds = resolve_targets(session, version, priority)
    tz, holidays, working_hours = sla_calendar_context(session, calendar)
    started_at = now
    arrival_deadline = deadline_after(session, calendar.id, working_hours, tz, holidays, started_at, arrival_seconds)
    completion_deadline = deadline_after(session, calendar.id, working_hours, tz, holidays, started_at, completion_seconds)
    sla = FieldSLAInstance(
        tenant_id=work_order.tenant_id,
        work_order_id=work_order.id,
        policy_id=policy.id,
        policy_version=version.version,
        calendar_id=calendar.id,
        timezone=calendar.timezone,
        arrival_target_seconds=arrival_seconds,
        completion_target_seconds=completion_seconds,
        arrival_deadline=arrival_deadline,
        completion_deadline=completion_deadline,
        started_at=started_at,
        paused_accumulated_seconds=0,
        status="ACTIVE",
        selected_reason=selected_reason,
        policy_snapshot={
            "policy_id": str(policy.id),
            "policy_code": policy.code,
            "policy_version": version.version,
            "definition": version.definition,
            "calendar_code": calendar.code,
            "calendar_id": str(calendar.id),
            "timezone": calendar.timezone,
            "arrival_target_seconds": arrival_seconds,
            "completion_target_seconds": completion_seconds,
            "selected_reason": selected_reason,
            "effective_at": now.isoformat(),
        },
        effective_at=now,
    )
    session.add(sla)
    session.flush()
    return sla


# ---------------------------------------------------------------------------
# Pause / resume
# ---------------------------------------------------------------------------
def pause_field_sla(session: Session, sla: FieldSLAInstance, *, reason: str | None = None,
                    policy_rule: str | None = None, actor: str | None = None) -> bool:
    if sla.status == "PAUSED" and sla.paused_at is not None:
        return False
    if sla.status == "COMPLETED":
        return False
    sla.status = "PAUSED"
    sla.paused_at = _now()
    session.add(FieldSLAPause(tenant_id=sla.tenant_id, sla_id=sla.id, paused_at=sla.paused_at,
                              reason=reason, policy_rule=policy_rule, actor=actor))
    session.flush()
    return True


def resume_field_sla(session: Session, sla: FieldSLAInstance) -> bool:
    if sla.status != "PAUSED" or sla.paused_at is None:
        return False
    from ...models import BusinessCalendar

    now = _now()
    calendar = session.get(BusinessCalendar, sla.calendar_id)
    tz, holidays, working_hours = sla_calendar_context(session, calendar)
    excluded = business_seconds_between(session, sla.calendar_id, working_hours, tz, holidays, sla.paused_at, now)
    sla.paused_accumulated_seconds += excluded
    sla.arrival_deadline = deadline_after(
        session, sla.calendar_id, working_hours, tz, holidays, sla.started_at,
        sla.arrival_target_seconds + sla.paused_accumulated_seconds)
    sla.completion_deadline = deadline_after(
        session, sla.calendar_id, working_hours, tz, holidays, sla.started_at,
        sla.completion_target_seconds + sla.paused_accumulated_seconds)
    pause_row = session.scalars(
        select(FieldSLAPause).where(FieldSLAPause.sla_id == sla.id, FieldSLAPause.resumed_at.is_(None))
        .order_by(FieldSLAPause.paused_at.desc())).first()
    if pause_row is not None:
        pause_row.resumed_at = now
        pause_row.elapsed_business_seconds = excluded
    sla.paused_at = None
    sla.status = "ACTIVE"
    session.flush()
    return True


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------
def evaluate_field_sla(session: Session, sla: FieldSLAInstance, *, now: datetime | None = None,
                       emit: bool = True, consumer: str = "field-sla-evaluator") -> dict:
    now = _aware(now or _now())
    if sla.status == "COMPLETED":
        return {"status": sla.status, "at_risk": False, "breached": False, "changed": False}
    if sla.paused_at is not None:
        return {"status": sla.status, "at_risk": False, "breached": False, "changed": False}
    from ...models import BusinessCalendar, VisitCheckIn

    calendar = session.get(BusinessCalendar, sla.calendar_id)
    tz, holidays, working_hours = sla_calendar_context(session, calendar)
    definition = sla.policy_snapshot.get("definition") or {}
    work_order = session.get(WorkOrder, sla.work_order_id)
    arrival_deadline = _aware(sla.arrival_deadline)
    completion_deadline = _aware(sla.completion_deadline)

    # Arrival is "met" once a check-in exists.
    arrived = session.scalars(
        select(VisitCheckIn.id).where(VisitCheckIn.work_order_id == sla.work_order_id).limit(1)).first() is not None

    def remaining(kind_deadline) -> int:
        if now >= kind_deadline:
            return 0
        return business_seconds_between(session, sla.calendar_id, working_hours, tz, holidays, now, kind_deadline)

    arrival_remaining = remaining(arrival_deadline)
    completion_remaining = remaining(completion_deadline)

    breached = False
    if (not arrived) and arrival_remaining <= 0:
        breached = True
    if completion_remaining <= 0:
        breached = True

    at_risk = False
    if not breached:
        thresholds = {t.get("target"): t.get("at_risk_pct") for t in definition.get("escalation", [])}
        if (not arrived) and sla.arrival_target_seconds > 0 and arrival_remaining <= sla.arrival_target_seconds * thresholds.get("ARRIVAL", 75) / 100.0:
            at_risk = True
        if sla.completion_target_seconds > 0 and completion_remaining <= sla.completion_target_seconds * thresholds.get("TIME_TO_COMPLETE", 75) / 100.0:
            at_risk = True

    changed = False
    result_status = sla.status
    if breached and sla.breach_at is None:
        sla.breach_at = now
        sla.status = "BREACHED"
        result_status = "BREACHED"
        changed = True
        if emit and work_order is not None:
            append_event(session, work_order, "work_order.sla_breached",
                         payload={"target_type": "ARRIVAL" if (not arrived) and arrival_remaining <= 0 else "TIME_TO_COMPLETE",
                                  "deadline": completion_deadline.isoformat(), "breach_at": now.isoformat()},
                         actor_type="system", actor_id=consumer, correlation_id=work_order.correlation_id)
            outbox(session, "workforce.work_order.sla_breached.v1", sla.tenant_id, work_order.correlation_id,
                   {"work_order_id": str(work_order.id), "work_order_number": work_order.work_order_number,
                    "breach_at": now.isoformat()})
    elif (not breached) and at_risk and sla.at_risk_at is None:
        sla.at_risk_at = now
        sla.status = "AT_RISK"
        result_status = "AT_RISK"
        changed = True
        if emit and work_order is not None:
            append_event(session, work_order, "work_order.sla_at_risk",
                         payload={"deadline": completion_deadline.isoformat(),
                                  "remaining_business_seconds": completion_remaining},
                         actor_type="system", actor_id=consumer, correlation_id=work_order.correlation_id)
            outbox(session, "workforce.work_order.sla_at_risk.v1", sla.tenant_id, work_order.correlation_id,
                   {"work_order_id": str(work_order.id), "work_order_number": work_order.work_order_number,
                    "at_risk_at": now.isoformat()})
    session.flush()
    return {"status": result_status, "at_risk": at_risk, "breached": breached, "changed": changed}


def reconcile_field_sla(session: Session, sla: FieldSLAInstance) -> dict:
    from ...models import BusinessCalendar

    calendar = session.get(BusinessCalendar, sla.calendar_id)
    tz, holidays, working_hours = sla_calendar_context(session, calendar)
    sla.arrival_deadline = deadline_after(
        session, sla.calendar_id, working_hours, tz, holidays, sla.started_at,
        sla.arrival_target_seconds + sla.paused_accumulated_seconds)
    sla.completion_deadline = deadline_after(
        session, sla.calendar_id, working_hours, tz, holidays, sla.started_at,
        sla.completion_target_seconds + sla.paused_accumulated_seconds)
    session.flush()
    return {"arrival_deadline": sla.arrival_deadline.isoformat(), "completion_deadline": sla.completion_deadline.isoformat()}
