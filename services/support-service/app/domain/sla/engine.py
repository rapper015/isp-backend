"""SLA selection, instantiation, timer behaviour and evaluation.

Authoritative SLA state is stored in the database (TicketSLA). Redis/Celery may
accelerate checks but never hold SLA truth. The engine is idempotent,
restart-safe and reconciliation-capable:

invariant:  deadline = deadline_after(started_at, target + paused_accumulated_seconds)

Pauses therefore extend deadlines by exactly the business time they excluded;
a paused SLA never advances; evaluation only ever derives from persisted
columns plus the business calendar.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...enums import SLA_STATUSES
from ...models import (
    BusinessCalendar,
    SLAPolicy,
    SLAPolicyVersion,
    SLATarget,
    Ticket,
    TicketComment,
    TicketSLA,
    TicketSLAPause,
)
from ...services.audit_service import append_event, outbox
from .calendar import business_seconds_between, deadline_after, default_working_hours, load_holidays

PRIORITY_ALL = "ALL"


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------
def select_policy(session: Session, tenant_id, *, ticket_type: str, category_id=None, subcategory_id=None, customer_tier=None) -> tuple[SLAPolicy, str]:
    """Pick the SLA policy for a ticket.

    Deterministic preference: the tenant's active policy wins; otherwise the
    global default. The reason is recorded so selection is auditable."""
    tenant_policies = list(
        session.scalars(
            select(SLAPolicy).where(SLAPolicy.tenant_id == tenant_id, SLAPolicy.is_active.is_(True)).order_by(SLAPolicy.created_at)
        )
    )
    if tenant_policies:
        return tenant_policies[0], f"tenant:{tenant_policies[0].code}"
    global_policies = list(
        session.scalars(select(SLAPolicy).where(SLAPolicy.tenant_id.is_(None), SLAPolicy.is_active.is_(True)).order_by(SLAPolicy.created_at))
    )
    if global_policies:
        return global_policies[0], f"global:{global_policies[0].code}"
    raise ValueError("no active SLA policy is configured")


def active_version(session: Session, policy: SLAPolicy) -> SLAPolicyVersion:
    version = session.scalars(
        select(SLAPolicyVersion).where(SLAPolicyVersion.policy_id == policy.id, SLAPolicyVersion.is_active.is_(True))
    ).first()
    if version is None:
        raise ValueError(f"SLA policy {policy.code!r} has no active version")
    return version


def resolve_targets(session: Session, version: SLAPolicyVersion, priority: str) -> tuple[int, int]:
    """(response_seconds, resolution_seconds) for a priority. Falls back to the
    ALL-priority row when no priority-specific row exists."""
    rows = list(session.scalars(select(SLATarget).where(SLATarget.version_id == version.id)))
    by = {(r.priority, r.kind): r.business_seconds for r in rows}

    def get(kind: str) -> int:
        if (priority, kind) in by:
            return by[(priority, kind)]
        if (PRIORITY_ALL, kind) in by:
            return by[(PRIORITY_ALL, kind)]
        return 4 * 3600 if kind == "RESPONSE" else 8 * 3600  # documented default

    return get("RESPONSE"), get("RESOLUTION")


# ---------------------------------------------------------------------------
# Instantiation / snapshot
# ---------------------------------------------------------------------------
def calendar_of(session: Session, calendar_id: uuid.UUID) -> BusinessCalendar:
    calendar = session.get(BusinessCalendar, calendar_id)
    if calendar is None:
        raise ValueError(f"business calendar {calendar_id} not found")
    return calendar


def sla_calendar_context(session: Session, calendar: BusinessCalendar):
    tz = _zoneinfo(calendar.timezone)
    holidays = load_holidays(session, calendar.id)
    working_hours = calendar.working_hours or default_working_hours()
    return tz, holidays, working_hours


def _zoneinfo(name: str):
    from zoneinfo import ZoneInfo

    try:
        return ZoneInfo(name or "UTC")
    except Exception:  # noqa: BLE001 — bad tz name falls back to UTC
        return ZoneInfo("UTC")


def instantiate_ticket_sla(
    session: Session,
    ticket: Ticket,
    policy: SLAPolicy,
    version: SLAPolicyVersion,
    calendar: BusinessCalendar,
    *,
    selected_reason: str,
    priority: str,
    effective_at: datetime | None = None,
) -> TicketSLA:
    """Create the authoritative SLA instance for a ticket with an immutable
    snapshot of the policy definition, calendar and resolved targets."""
    now = effective_at or _now()
    response_seconds, resolution_seconds = resolve_targets(session, version, priority)
    tz, holidays, working_hours = sla_calendar_context(session, calendar)
    started_at = now
    response_deadline = deadline_after(session, calendar.id, working_hours, tz, holidays, started_at, response_seconds)
    resolution_deadline = deadline_after(session, calendar.id, working_hours, tz, holidays, started_at, resolution_seconds)
    sla = TicketSLA(
        tenant_id=ticket.tenant_id,
        ticket_id=ticket.id,
        policy_id=policy.id,
        policy_version=version.version,
        calendar_id=calendar.id,
        timezone=calendar.timezone,
        response_target_seconds=response_seconds,
        resolution_target_seconds=resolution_seconds,
        response_deadline=response_deadline,
        resolution_deadline=resolution_deadline,
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
            "working_hours": calendar.working_hours or default_working_hours(),
            "response_target_seconds": response_seconds,
            "resolution_target_seconds": resolution_seconds,
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
def pause_sla(session: Session, sla: TicketSLA, calendar: BusinessCalendar) -> bool:
    """Pause the clock. Returns True if a new pause interval was opened."""
    if sla.status == "PAUSED" and sla.paused_at is not None:
        return False
    if sla.status == "COMPLETED":
        return False
    sla.status = "PAUSED"
    sla.paused_at = _now()
    session.add(TicketSLAPause(tenant_id=sla.tenant_id, sla_id=sla.id, paused_at=sla.paused_at))
    session.flush()
    return True


def resume_sla(session: Session, sla: TicketSLA, calendar: BusinessCalendar) -> bool:
    """Resume the clock: extend deadlines by the exact business time the pause
    excluded, then recompute. Returns True if a pause interval was closed."""
    if sla.status != "PAUSED" or sla.paused_at is None:
        return False
    now = _now()
    tz, holidays, working_hours = sla_calendar_context(session, calendar)
    excluded = business_seconds_between(session, calendar.id, working_hours, tz, holidays, sla.paused_at, now)
    sla.paused_accumulated_seconds += excluded
    sla.response_deadline = deadline_after(
        session, calendar.id, working_hours, tz, holidays, sla.started_at,
        sla.response_target_seconds + sla.paused_accumulated_seconds,
    )
    sla.resolution_deadline = deadline_after(
        session, calendar.id, working_hours, tz, holidays, sla.started_at,
        sla.resolution_target_seconds + sla.paused_accumulated_seconds,
    )
    pause_row = session.scalars(
        select(TicketSLAPause)
        .where(TicketSLAPause.sla_id == sla.id, TicketSLAPause.resumed_at.is_(None))
        .order_by(TicketSLAPause.paused_at.desc())
    ).first()
    if pause_row is not None:
        pause_row.resumed_at = now
        pause_row.elapsed_business_seconds = excluded
    sla.paused_at = None
    sla.status = "ACTIVE"
    session.flush()
    return True


# ---------------------------------------------------------------------------
# Response met tracking
# ---------------------------------------------------------------------------
def response_met(session: Session, ticket_id: uuid.UUID) -> bool:
    """A first human response has been sent when an agent public reply exists."""
    row = session.scalars(
        select(TicketComment.id)
        .where(
            TicketComment.ticket_id == ticket_id,
            TicketComment.kind == "PUBLIC_REPLY",
            TicketComment.sender_type == "AGENT",
            TicketComment.visibility == "PUBLIC",
        )
        .limit(1)
    ).first()
    return row is not None


def _aware(dt: datetime) -> datetime:
    """SQLite returns naive datetimes; normalize to aware UTC for comparison."""
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


# ---------------------------------------------------------------------------
# Evaluation (at-risk / breach)
# ---------------------------------------------------------------------------
def evaluate_sla(
    session: Session,
    sla: TicketSLA,
    *,
    now: datetime | None = None,
    emit: bool = True,
    consumer: str = "sla-evaluator",
) -> dict:
    """Idempotently evaluate one SLA instance.

    Returns {'status', 'at_risk', 'breached', 'changed'} — 'changed' is True
    only when a new at-risk or breach state was recorded so events and
    escalations fire exactly once."""
    now = _aware(now or _now())
    if sla.status == "COMPLETED":
        return {"status": sla.status, "at_risk": False, "breached": False, "changed": False}
    if sla.paused_at is not None:
        return {"status": sla.status, "at_risk": False, "breached": False, "changed": False}
    calendar = calendar_of(session, sla.calendar_id)
    tz, holidays, working_hours = sla_calendar_context(session, calendar)
    definition = sla.policy_snapshot.get("definition") or {}
    ticket = session.get(Ticket, sla.ticket_id)
    response_deadline = _aware(sla.response_deadline)
    resolution_deadline = _aware(sla.resolution_deadline)

    def remaining(kind_deadline) -> int:
        if now >= kind_deadline:
            return 0
        return business_seconds_between(session, sla.calendar_id, working_hours, tz, holidays, now, kind_deadline)

    response_remaining = remaining(response_deadline)
    resolution_remaining = remaining(resolution_deadline)
    response_met_flag = response_met(session, sla.ticket_id)

    breached = False
    if not response_met_flag and response_remaining <= 0:
        breached = True
    if resolution_remaining <= 0:
        breached = True

    at_risk_thresholds = {t.get("target"): t.get("at_risk_pct") for t in definition.get("escalation", [])}
    at_risk = False
    if not breached:
        res_pct = at_risk_thresholds.get("RESOLUTION", 75)
        if sla.resolution_target_seconds > 0 and resolution_remaining <= sla.resolution_target_seconds * res_pct / 100.0:
            at_risk = True
        res_pct_response = at_risk_thresholds.get("RESPONSE", 75)
        if (not response_met_flag) and sla.response_target_seconds > 0 and response_remaining <= sla.response_target_seconds * res_pct_response / 100.0:
            at_risk = True

    changed = False
    result_status = sla.status
    if breached and sla.breach_at is None:
        sla.breach_at = now
        sla.status = "BREACHED"
        result_status = "BREACHED"
        changed = True
        if emit and ticket is not None:
            append_event(
                session, ticket, "ticket.sla_breached",
                payload={"target_type": "RESOLUTION" if resolution_remaining <= 0 else "RESPONSE",
                         "deadline": resolution_deadline.isoformat(),
                         "breach_at": now.isoformat(),
                         "elapsed_business_seconds": None},
                actor_type="system", actor_id=consumer,
                correlation_id=ticket.correlation_id,
            )
            outbox(session, "support.ticket.sla_breached.v1", sla.tenant_id, ticket.correlation_id,
                   {"ticket_id": str(ticket.id), "ticket_number": ticket.ticket_number,
                    "sla_id": str(sla.id), "breach_at": now.isoformat()})
    elif (not breached) and at_risk and sla.at_risk_at is None:
        sla.at_risk_at = now
        sla.status = "AT_RISK"
        result_status = "AT_RISK"
        changed = True
        if emit and ticket is not None:
            append_event(
                session, ticket, "ticket.sla_at_risk",
                payload={"target_type": "RESOLUTION" if resolution_remaining <= sla.resolution_target_seconds * 0.75 else "RESPONSE",
                         "deadline": resolution_deadline.isoformat(),
                         "remaining_business_seconds": resolution_remaining},
                actor_type="system", actor_id=consumer,
                correlation_id=ticket.correlation_id,
            )
            outbox(session, "support.ticket.sla_at_risk.v1", sla.tenant_id, ticket.correlation_id,
                   {"ticket_id": str(ticket.id), "ticket_number": ticket.ticket_number,
                    "sla_id": str(sla.id), "at_risk_at": now.isoformat()})
    session.flush()
    return {"status": result_status, "at_risk": at_risk, "breached": breached, "changed": changed}


# ---------------------------------------------------------------------------
# Recalculation & reconciliation
# ---------------------------------------------------------------------------
def recalculate_for_priority(session: Session, sla: TicketSLA, version: SLAPolicyVersion, priority: str, *, now: datetime | None = None) -> dict:
    """Priority change: preserve the elapsed fraction and set fresh deadlines
    from the new targets. Assignment never resets the SLA unless the policy
    authorizes it; this function is only for priority recalculation."""
    now = now or _now()
    calendar = calendar_of(session, sla.calendar_id)
    tz, holidays, working_hours = sla_calendar_context(session, calendar)
    new_response, new_resolution = resolve_targets(session, version, priority)

    def elapsed_fraction(target_seconds: int) -> float:
        if target_seconds <= 0:
            return 0.0
        elapsed = business_seconds_between(session, sla.calendar_id, working_hours, tz, holidays, sla.started_at, now) - sla.paused_accumulated_seconds
        return max(0.0, min(1.0, elapsed / target_seconds))

    sla.response_target_seconds = new_response
    sla.resolution_target_seconds = new_resolution
    sla.started_at = now
    sla.paused_accumulated_seconds = 0
    response_remaining = int(new_response * (1.0 - elapsed_fraction(sla.response_target_seconds)))
    resolution_remaining = int(new_resolution * (1.0 - elapsed_fraction(sla.resolution_target_seconds)))
    sla.response_deadline = deadline_after(session, sla.calendar_id, working_hours, tz, holidays, now, response_remaining)
    sla.resolution_deadline = deadline_after(session, sla.calendar_id, working_hours, tz, holidays, now, resolution_remaining)
    if sla.paused_at is None:
        sla.status = "ACTIVE"
    session.flush()
    return {"response_deadline": sla.response_deadline, "resolution_deadline": sla.resolution_deadline}


def restart_sla_run(session: Session, sla: TicketSLA, *, now: datetime | None = None) -> dict:
    """Reopen policy RESTART: fresh full-target deadlines from now."""
    now = now or _now()
    calendar = calendar_of(session, sla.calendar_id)
    tz, holidays, working_hours = sla_calendar_context(session, calendar)
    sla.started_at = now
    sla.paused_accumulated_seconds = 0
    sla.paused_at = None
    sla.response_deadline = deadline_after(session, sla.calendar_id, working_hours, tz, holidays, now, sla.response_target_seconds)
    sla.resolution_deadline = deadline_after(session, sla.calendar_id, working_hours, tz, holidays, now, sla.resolution_target_seconds)
    sla.status = "ACTIVE"
    session.flush()
    return {"response_deadline": sla.response_deadline, "resolution_deadline": sla.resolution_deadline}


def reconcile_sla(session: Session, sla: TicketSLA) -> dict:
    """Reconcile a SLA instance against the invariant
    deadline = deadline_after(started_at, target + paused_accumulated).
    Repairs drift in persisted deadlines (used by repair commands)."""
    calendar = calendar_of(session, sla.calendar_id)
    tz, holidays, working_hours = sla_calendar_context(session, calendar)
    expected_response = deadline_after(
        session, sla.calendar_id, working_hours, tz, holidays, sla.started_at,
        sla.response_target_seconds + sla.paused_accumulated_seconds,
    )
    expected_resolution = deadline_after(
        session, sla.calendar_id, working_hours, tz, holidays, sla.started_at,
        sla.resolution_target_seconds + sla.paused_accumulated_seconds,
    )
    sla.response_deadline = expected_response
    sla.resolution_deadline = expected_resolution
    session.flush()
    return {"response_deadline": sla.response_deadline.isoformat(), "resolution_deadline": sla.resolution_deadline.isoformat()}
