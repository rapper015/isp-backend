"""SLA policy management service: versioned policies with immutable published
versions, target CRUD, activation and validation. Published versions can never
be edited; activating a new version starts a new immutable revision."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.exceptions import SLAError, ValidationError
from ..domain.sla.engine import active_version, calendar_of, instantiate_ticket_sla
from ..enums import SLA_TARGET_KINDS
from ..models import (
    BusinessCalendar,
    SLAPolicy,
    SLAPolicyVersion,
    SLATarget,
    Ticket,
    TicketSLA,
)
from ..services.audit_service import audit, correlation


def _now() -> datetime:
    return datetime.now(timezone.utc)


def validate_definition(definition: dict) -> None:
    if not isinstance(definition, dict):
        raise ValidationError("SLA definition must be an object")
    pause_states = definition.get("pause_on_states", [])
    if not isinstance(pause_states, list):
        raise ValidationError("pause_on_states must be a list")
    for state in pause_states:
        from ..enums import TICKET_STATES

        if state not in TICKET_STATES:
            raise ValidationError(f"pause_on_states contains unknown state {state!r}")
    reopen = definition.get("reopen_policy", "RESTART")
    if reopen not in ("RESTART", "CONTINUE"):
        raise ValidationError("reopen_policy must be RESTART or CONTINUE")
    for threshold in definition.get("escalation", []):
        if threshold.get("target") not in ("RESPONSE", "RESOLUTION"):
            raise ValidationError("escalation target must be RESPONSE or RESOLUTION")
        if not (0 < threshold.get("at_risk_pct", 0) < 100):
            raise ValidationError("escalation at_risk_pct must be between 0 and 100")


def create_policy(session: Session, tenant_id, *, code: str, name: str, actor: str | None = None) -> SLAPolicy:
    existing = session.scalars(select(SLAPolicy).where(SLAPolicy.tenant_id == tenant_id, SLAPolicy.code == code)).first()
    if existing is not None:
        raise SLAError(f"SLA policy {code!r} already exists for tenant")
    policy = SLAPolicy(tenant_id=tenant_id, code=code, name=name, is_active=False, current_version=0)
    session.add(policy)
    session.flush()
    audit(session, tenant_id, "support.sla.policy_created", "sla_policy", str(policy.id), actor=actor,
          correlation_id=correlation(None), safe_after={"code": code, "name": name})
    return policy


def create_version(session: Session, tenant_id, policy_id, *, definition: dict, targets: list[dict],
                   actor: str | None = None, activate: bool = False) -> SLAPolicyVersion:
    policy = session.get(SLAPolicy, policy_id)
    if policy is None or policy.tenant_id != tenant_id:
        raise SLAError("SLA policy not found")
    validate_definition(definition)
    version = SLAPolicyVersion(
        tenant_id=tenant_id, policy_id=policy.id, version=policy.current_version + 1,
        is_active=False, activated_at=None, activated_by=None,
        definition=definition,
    )
    session.add(version)
    session.flush()
    for target in targets:
        kind = (target.get("kind") or "").upper()
        if kind not in SLA_TARGET_KINDS and kind not in ("RESPONSE", "RESOLUTION"):
            raise SLAError(f"invalid SLA target kind {kind!r}")
        priority = (target.get("priority") or "ALL").upper()
        seconds = int(target.get("business_seconds", 0))
        if seconds <= 0:
            raise SLAError("SLA target business_seconds must be positive")
        session.add(SLATarget(tenant_id=tenant_id, version_id=version.id, priority=priority, kind=kind, business_seconds=seconds))
    if activate:
        # Deactivate other versions first, then activate this one (never itself).
        _deactivate_others(session, policy)
        version.is_active = True
        version.activated_at = _now()
        version.activated_by = actor
        policy.current_version = version.version
        policy.is_active = True
    session.flush()
    audit(session, tenant_id, "support.sla.version_created", "sla_policy_version", str(version.id), actor=actor,
          correlation_id=correlation(None), safe_after={"policy": policy.code, "version": version.version, "active": activate})
    return version


def _deactivate_others(session: Session, policy: SLAPolicy) -> None:
    for other in session.scalars(select(SLAPolicyVersion).where(
            SLAPolicyVersion.policy_id == policy.id, SLAPolicyVersion.is_active.is_(True))):
        other.is_active = False


def activate_version(session: Session, tenant_id, policy_id, version: int, actor: str | None = None) -> SLAPolicyVersion:
    policy = session.get(SLAPolicy, policy_id)
    if policy is None or policy.tenant_id != tenant_id:
        raise SLAError("SLA policy not found")
    version_row = session.scalars(
        select(SLAPolicyVersion).where(SLAPolicyVersion.policy_id == policy_id, SLAPolicyVersion.version == version)
    ).first()
    if version_row is None:
        raise SLAError(f"SLA policy version {version} not found")
    _deactivate_others(session, policy)
    version_row.is_active = True
    version_row.activated_at = _now()
    version_row.activated_by = actor
    policy.current_version = version
    policy.is_active = True
    session.flush()
    audit(session, tenant_id, "support.sla.version_activated", "sla_policy_version", str(version_row.id), actor=actor,
          correlation_id=correlation(None), safe_after={"policy": policy.code, "version": version})
    return version_row


def get_ticket_sla(session: Session, ticket: Ticket) -> TicketSLA | None:
    return session.scalars(select(TicketSLA).where(TicketSLA.ticket_id == ticket.id)).first()


def sla_timeline(session: Session, sla: TicketSLA) -> dict:
    from ..models import TicketEvent, TicketSLAPause

    events = list(session.scalars(
        select(TicketEvent).where(TicketEvent.ticket_id == sla.ticket_id).order_by(TicketEvent.aggregate_version)))
    pauses = list(session.scalars(select(TicketSLAPause).where(TicketSLAPause.sla_id == sla.id).order_by(TicketSLAPause.paused_at)))
    return {
        "sla_id": str(sla.id),
        "status": sla.status,
        "policy_id": str(sla.policy_id),
        "policy_version": sla.policy_version,
        "timezone": sla.timezone,
        "response_deadline": sla.response_deadline.isoformat() if sla.response_deadline else None,
        "resolution_deadline": sla.resolution_deadline.isoformat() if sla.resolution_deadline else None,
        "at_risk_at": sla.at_risk_at.isoformat() if sla.at_risk_at else None,
        "breach_at": sla.breach_at.isoformat() if sla.breach_at else None,
        "paused_accumulated_seconds": sla.paused_accumulated_seconds,
        "pauses": [{"paused_at": p.paused_at.isoformat(), "resumed_at": p.resumed_at.isoformat() if p.resumed_at else None,
                    "elapsed_business_seconds": p.elapsed_business_seconds} for p in pauses],
        "events": [{"version": e.aggregate_version, "type": e.event_type, "at": e.created_at.isoformat()} for e in events
                   if e.event_type.startswith("ticket.sla_")],
    }


def apply_sla_override(session: Session, tenant_id, ticket: Ticket, *, response_deadline, resolution_deadline,
                       reason: str, actor: str, correlation_id: str | None = None) -> TicketSLA:
    """Authorized, audited override of SLA deadlines. Historical snapshot and
    timer state are preserved; only the deadlines are moved with a reason."""
    sla = get_ticket_sla(session, ticket)
    if sla is None:
        raise SLAError("ticket has no SLA instance")
    sla.response_deadline = response_deadline
    sla.resolution_deadline = resolution_deadline
    sla.selected_reason = f"OVERRIDE: {reason}"
    sla.status = "ACTIVE"
    from ..services.audit_service import append_event

    append_event(session, ticket, "ticket.sla_override",
                 payload={"reason": reason, "response_deadline": response_deadline.isoformat(),
                          "resolution_deadline": resolution_deadline.isoformat()},
                 actor_type="agent", actor_id=actor, correlation_id=correlation_id or correlation(None))
    audit(session, tenant_id, "support.sla.override", "ticket", str(ticket.id), actor=actor, reason=reason,
          correlation_id=correlation_id or correlation(None),
          safe_after={"response_deadline": response_deadline.isoformat(), "resolution_deadline": resolution_deadline.isoformat()})
    session.flush()
    return sla
