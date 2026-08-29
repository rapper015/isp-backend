"""Escalation engine: versioned-ish, explainable and fully auditable.

Escalations are triggered by SLA risk/breach, missing assignment, missing
progress, repeated reopens, P1/P2 severity, VIP/business customers, security or
financial impact and failed support actions. Every automatic escalation records
a TicketEscalation row, an immutable event and (for public-relevant triggers) a
notified recipient list. Every escalation is explainable via its trigger +
reason."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.exceptions import ValidationError
from ..integrations.base import get_adapter
from ..models import SupportAction, Ticket, TicketEscalation, TicketEvent, TicketSLA
from ..services.audit_service import append_event, outbox
from . import assignment_service


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Trigger detection
# ---------------------------------------------------------------------------
def detect_triggers(session: Session, tenant_id, ticket: Ticket, *, now: datetime | None = None) -> list[dict]:
    now = now or _now()
    triggers: list[dict] = []

    sla = session.scalars(select(TicketSLA).where(TicketSLA.ticket_id == ticket.id)).first()
    if sla is not None:
        if sla.status == "BREACHED" or (sla.breach_at is not None):
            triggers.append({"trigger": "SLA_BREACH", "level": 2,
                             "reason": f"SLA breach on {sla.breach_at.isoformat()}"})
        elif sla.status == "AT_RISK" or (sla.at_risk_at is not None):
            triggers.append({"trigger": "SLA_AT_RISK", "level": 1,
                             "reason": f"SLA at risk (deadline {sla.resolution_deadline.isoformat()})"})

    if ticket.status in ("NEW", "TRIAGE") and ticket.assigned_agent_id is None:
        stale = (now - (ticket.updated_at if ticket.updated_at.tzinfo else ticket.updated_at.replace(tzinfo=timezone.utc))).total_seconds()
        if stale > 4 * 3600:
            triggers.append({"trigger": "NO_ASSIGNMENT", "level": 1, "reason": "unassigned for over 4 hours"})

    if ticket.status in ("ASSIGNED", "IN_PROGRESS", "PENDING_INTERNAL_TEAM") and ticket.assigned_agent_id:
        updated = ticket.updated_at if ticket.updated_at.tzinfo else ticket.updated_at.replace(tzinfo=timezone.utc)
        if (now - updated) > timedelta(hours=24):
            triggers.append({"trigger": "NO_PROGRESS", "level": 1, "reason": "no progress for over 24 hours"})

    if ticket.reopened_count >= 3:
        triggers.append({"trigger": "REPEATED_REOPEN", "level": 2, "reason": f"reopened {ticket.reopened_count} times"})

    if ticket.priority in ("P1_CRITICAL", "P2_HIGH"):
        triggers.append({"trigger": "SEVERITY_P1_P2", "level": 1, "reason": f"priority {ticket.priority}"})

    if ticket.customer_tier and ticket.customer_tier.upper() in ("VIP", "BUSINESS", "ENTERPRISE", "CORPORATE"):
        triggers.append({"trigger": "VIP_CUSTOMER", "level": 1, "reason": f"customer tier {ticket.customer_tier}"})

    if ticket.ticket_type == "SECURITY_REPORT":
        triggers.append({"trigger": "SECURITY_IMPACT", "level": 2, "reason": "security report"})

    if ticket.ticket_type in ("BILLING_DISPUTE", "BILLING_QUERY", "PAYMENT_QUERY"):
        triggers.append({"trigger": "FINANCIAL_IMPACT", "level": 1, "reason": f"type {ticket.ticket_type}"})

    failed_actions = list(session.scalars(
        select(SupportAction).where(SupportAction.ticket_id == ticket.id,
                                    SupportAction.status.in_(("FAILED", "TIMED_OUT", "MANUAL_INTERVENTION_REQUIRED")))))
    if failed_actions:
        triggers.append({"trigger": "FAILED_SUPPORT_ACTION", "level": 1,
                         "reason": f"{len(failed_actions)} failed support action(s)"})

    return triggers


def open_escalations(session: Session, ticket_id) -> list[TicketEscalation]:
    return list(session.scalars(
        select(TicketEscalation).where(TicketEscalation.ticket_id == ticket_id, TicketEscalation.status == "OPEN")))


def evaluate_ticket(session: Session, tenant_id, ticket: Ticket, *, actor: str = "system",
                    correlation_id: str | None = None) -> list[str]:
    """Detect new triggers and escalate only for triggers without an open
    escalation. Returns the list of triggers that fired."""
    fired: list[str] = []
    existing_open = {e.trigger for e in open_escalations(session, ticket.id)}
    for detection in detect_triggers(session, tenant_id, ticket):
        trigger = detection["trigger"]
        if trigger in existing_open:
            continue
        execute_escalation(session, ticket, trigger=trigger, reason=detection["reason"],
                           level=detection["level"], actor=actor, correlation_id=correlation_id)
        fired.append(trigger)
        existing_open.add(trigger)
    session.flush()
    return fired


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------
def execute_escalation(session: Session, ticket: Ticket, *, trigger: str, reason: str,
                       level: int | None = None, actor: str = "system",
                       correlation_id: str | None = None) -> TicketEscalation:
    level = level or ticket.escalation_level + 1
    ticket.escalation_level = max(ticket.escalation_level, level)
    actions: list[str] = []
    recipients: list[str] = []

    notifications = get_adapter("notifications")
    sla = session.scalars(select(TicketSLA).where(TicketSLA.ticket_id == ticket.id)).first()

    if trigger in ("SLA_AT_RISK", "SLA_BREACH") and sla is not None:
        policy_actions = (sla.policy_snapshot.get("definition") or {}).get("escalation", [])
        for threshold in policy_actions:
            if threshold.get("level", 1) <= level:
                action = threshold.get("action")
                if action and action not in actions:
                    actions.append(action)
        if trigger == "SLA_BREACH" and "NOTIFY_CUSTOMER" not in actions:
            actions.append("NOTIFY_CUSTOMER")

    if trigger in ("NO_ASSIGNMENT", "NO_PROGRESS"):
        actions.append("NOTIFY_TEAM_LEAD")
        actions.append("REASSIGN_QUEUE")

    if trigger in ("REPEATED_REOPEN", "SECURITY_IMPACT", "FINANCIAL_IMPACT"):
        actions.append("REQUIRE_MANAGEMENT_REVIEW")
        actions.append("ADD_SUPERVISOR_WATCHER")

    if trigger in ("FAILED_SUPPORT_ACTION", "FAILED_OSS_ORDER"):
        actions.append("NOTIFY_TEAM_LEAD")
        actions.append("REQUIRE_MANAGEMENT_REVIEW")

    if ticket.priority in ("P1_CRITICAL", "P2_HIGH"):
        actions.append("CREATE_NOC_INVESTIGATION")

    # De-duplicate and resolve each action.
    for action in dict.fromkeys(actions):
        if action == "NOTIFY_AGENT" and ticket.assigned_agent_id:
            notifications.send(channel="push", recipient=ticket.assigned_agent_id, template="sla_at_risk",
                               variables={"ticket_number": ticket.ticket_number}, ticket_id=ticket.id,
                               correlation_id=correlation_id)
            recipients.append(f"agent:{ticket.assigned_agent_id}")
        elif action == "NOTIFY_TEAM_LEAD":
            notifications.send(channel="email", recipient=f"team-lead:{ticket.assigned_team_id}", template="escalation",
                               variables={"ticket_number": ticket.ticket_number, "trigger": trigger}, ticket_id=ticket.id,
                               correlation_id=correlation_id)
            recipients.append(f"team-lead:{ticket.assigned_team_id}")
        elif action == "NOTIFY_CUSTOMER":
            notifications.send(channel="email", recipient=f"customer:{ticket.customer_id}", template="escalation_update",
                               variables={"ticket_number": ticket.ticket_number}, ticket_id=ticket.id,
                               correlation_id=correlation_id)
            recipients.append(f"customer:{ticket.customer_id}")
        elif action == "REASSIGN_QUEUE":
            from ..enums import ROUTING_STRATEGIES  # noqa: F401

            try:
                assignment_service.route_ticket(session, ticket.tenant_id, ticket, actor=actor)
            except Exception:  # noqa: BLE001
                pass
        elif action == "ADD_SUPERVISOR_WATCHER":
            from .ticket_service import add_watcher

            add_watcher(session, ticket.tenant_id, ticket.id, watcher_type="SUPERVISOR",
                        watcher_id="supervisor", actor=actor, correlation_id=correlation_id)
        elif action == "RAISE_PRIORITY":
            from .ticket_service import change_priority

            from ..domain.priority import priority_rank

            if priority_rank(ticket.priority) > 0:
                try:
                    change_priority(session, ticket.tenant_id, ticket.id,
                                    priority="P1_CRITICAL" if ticket.priority != "P1_CRITICAL" else ticket.priority,
                                    reason=f"auto-escalation: {trigger}", actor=actor, correlation_id=correlation_id)
                except Exception:  # noqa: BLE001
                    pass
        elif action == "CREATE_NOC_INVESTIGATION":
            try:
                get_adapter("nms").create_noc_investigation(ticket_id=str(ticket.id), actor=actor, correlation_id=correlation_id)
            except Exception:  # noqa: BLE001
                pass

    escalation = TicketEscalation(
        tenant_id=ticket.tenant_id, ticket_id=ticket.id, level=level, trigger=trigger,
        reason=reason, actions=list(dict.fromkeys(actions)), recipients=recipients,
        status="OPEN", raised_by=actor, raised_at=_now(),
    )
    session.add(escalation)
    session.flush()
    append_event(session, ticket, "ticket.escalated",
                 payload={"trigger": trigger, "level": level, "reason": reason, "actions": escalation.actions},
                 actor_type="system" if actor == "system" else "agent", actor_id=actor,
                 correlation_id=correlation_id or ticket.correlation_id)
    outbox(session, "support.ticket.escalated.v1", ticket.tenant_id, correlation_id or ticket.correlation_id,
           {"ticket_id": str(ticket.id), "ticket_number": ticket.ticket_number, "trigger": trigger, "level": level})
    return escalation


def resolve_escalation(session: Session, ticket: Ticket, *, trigger: str | None = None, actor: str = "system") -> None:
    for escalation in open_escalations(session, ticket.id):
        if trigger is None or escalation.trigger == trigger:
            escalation.status = "RESOLVED"
            escalation.resolved_at = _now()
    session.flush()
