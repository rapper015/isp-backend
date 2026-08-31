"""Ticket command service: creation, validated lifecycle commands, SLA timer
effects, immutable events, outbox publication, assignment and related-record
linking.

No code may set ticket.status directly; every command goes through the state
machine. Domain rules live here, never in views, serializers or tasks."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain import numbering
from ..domain.exceptions import DuplicateError, NotFoundError, StateTransitionError, ValidationError
from ..domain.priority import calculate_priority, severity_for_priority, validate_priority
from ..domain.sla import engine as sla_engine
from ..enums import (
    RESOLUTION_CODES,
    SERVICE_ORDER_REQUIRED_TYPES,
    SOURCE_CHANNELS,
    TICKET_STATES,
    TICKET_TYPES,
)
from ..events import publish_outbox
from ..models import (
    Ticket,
    TicketCategory,
    TicketComment,
    TicketRelationship,
    TicketResolution,
    TicketSubcategory,
    TicketTag,
    TicketWatcher,
)
from ..services.audit_service import append_event, correlation, outbox
from . import assignment_service, catalog_service, csat_service, diagnostic_service, sla_service


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_ticket_or_404(session: Session, tenant_id, ticket_id: uuid.UUID) -> Ticket:
    ticket = session.get(Ticket, ticket_id)
    if ticket is None or ticket.tenant_id != tenant_id:
        raise NotFoundError("ticket not found")
    return ticket


def get_ticket_by_number(session: Session, tenant_id, ticket_number: str) -> Ticket:
    ticket = session.scalars(
        select(Ticket).where(Ticket.tenant_id == tenant_id, Ticket.ticket_number == ticket_number)
    ).first()
    if ticket is None:
        raise NotFoundError("ticket not found")
    return ticket


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------
def create_ticket(
    session: Session,
    tenant_id,
    *,
    ticket_type: str,
    subject: str,
    description: str,
    customer_id: str | None = None,
    customer_number: str | None = None,
    customer_name: str | None = None,
    customer_tier: str | None = None,
    service_subscription_id: str | None = None,
    subscriber_username: str | None = None,
    service_location_id: str | None = None,
    billing_account_id: str | None = None,
    franchise_id: str | None = None,
    reseller_id: str | None = None,
    branch_id: str | None = None,
    category_code: str | None = None,
    subcategory_code: str | None = None,
    source_channel: str = "CUSTOMER_PORTAL",
    impact: str = "MEDIUM",
    urgency: str = "MEDIUM",
    priority: str | None = None,
    correlation_id: str | None = None,
    idempotency_key: str | None = None,
    actor: str = "system",
    actor_type: str = "system",
    extra_tags: list | None = None,
) -> Ticket:
    """Create a ticket, allocate its immutable number, attach an SLA, publish
    the creation event and request a diagnostic snapshot (fail-open)."""
    request_id = correlation(correlation_id)
    if idempotency_key:
        existing = session.scalars(
            select(Ticket).where(Ticket.tenant_id == tenant_id, Ticket.idempotency_key == idempotency_key)
        ).first()
        if existing is not None:
            return existing
    ticket_type = ticket_type.upper()
    if ticket_type not in TICKET_TYPES:
        raise ValidationError(f"invalid ticket type {ticket_type!r}")
    if source_channel.upper() not in SOURCE_CHANNELS:
        raise ValidationError(f"invalid source channel {source_channel!r}")
    if not subject or not description:
        raise ValidationError("subject and description are required")
    impact = impact.upper()
    urgency = urgency.upper()
    calculated_priority = validate_priority(priority) if priority else calculate_priority(impact, urgency)
    severity = severity_for_priority(calculated_priority)

    category = None
    subcategory = None
    if category_code:
        category = session.scalars(
            select(TicketCategory).where(
                TicketCategory.code == category_code.upper(),
                (TicketCategory.tenant_id == tenant_id) | (TicketCategory.tenant_id.is_(None)),
            )
        ).first()
        if category is None:
            raise ValidationError(f"unknown category {category_code!r}")
    if subcategory_code:
        subcategory = session.scalars(
            select(TicketSubcategory).where(
                TicketSubcategory.code == subcategory_code.upper(),
                (TicketSubcategory.category_id == (category.id if category else None)) if category else TicketSubcategory.code.is_(None),
            )
        ).first() if category else None

    ticket_number = numbering.next_ticket_number(session, tenant_id)
    ticket = Ticket(
        tenant_id=tenant_id,
        ticket_number=ticket_number,
        ticket_type=ticket_type,
        category_id=category.id if category else None,
        subcategory_id=subcategory.id if subcategory else None,
        subject=subject.strip(),
        description=description.strip(),
        customer_id=customer_id,
        customer_number=customer_number,
        customer_name=customer_name,
        customer_tier=customer_tier,
        service_subscription_id=service_subscription_id,
        subscriber_username=subscriber_username,
        service_location_id=service_location_id,
        billing_account_id=billing_account_id,
        franchise_id=franchise_id,
        reseller_id=reseller_id,
        branch_id=branch_id,
        source_channel=source_channel.upper(),
        status="NEW",
        customer_status="SUBMITTED",
        impact=impact,
        urgency=urgency,
        priority=calculated_priority,
        severity=severity,
        created_by=actor,
        created_by_type=actor_type,
        correlation_id=request_id,
        idempotency_key=idempotency_key,
        csat_eligible=False,
    )
    session.add(ticket)
    session.flush()

    # SLA instantiation (fail-open: a missing default is a real config error).
    policy, reason = sla_engine.select_policy(session, tenant_id, ticket_type=ticket_type, category_id=category.id if category else None)
    version = sla_engine.active_version(session, policy)
    calendar = catalog_service.get_or_create_calendar(session, tenant_id)
    sla = sla_engine.instantiate_ticket_sla(session, ticket, policy, version, calendar, selected_reason=reason, priority=calculated_priority)
    ticket.sla_policy_id = policy.id
    ticket.sla_version = version.version
    ticket.response_deadline = sla.response_deadline
    ticket.resolution_deadline = sla.resolution_deadline
    ticket.sla_status = sla.status

    append_event(session, ticket, "ticket.created",
                 payload={"ticket_number": ticket_number, "ticket_type": ticket_type, "priority": calculated_priority,
                          "source_channel": source_channel.upper()},
                 actor_type=actor_type, actor_id=actor, correlation_id=request_id)
    publish_outbox(session, "support.ticket.created.v1", {
        "ticket_id": str(ticket.id), "ticket_number": ticket_number, "ticket_type": ticket_type,
        "priority": calculated_priority, "customer_id": customer_id,
    }, tenant_id=tenant_id, correlation_id=request_id, idempotency_key=idempotency_key)

    if extra_tags:
        for tag in extra_tags[:20]:
            session.add(TicketTag(tenant_id=tenant_id, ticket_id=ticket.id, tag=tag.strip()))

    # Assignment routing (fail-open; unassigned queue is acceptable).
    try:
        assignment_service.route_ticket(session, tenant_id, ticket, actor=actor)
    except Exception:  # noqa: BLE001 — routing must never block ticket creation
        pass

    # Diagnostic snapshot (fail-open, non-blocking).
    try:
        diagnostic_service.capture_diagnostic_snapshot(session, tenant_id, ticket, actor=actor, correlation_id=request_id, emit_event=False)
    except Exception:  # noqa: BLE001
        pass

    session.flush()
    return ticket


# ---------------------------------------------------------------------------
# Lifecycle command core
# ---------------------------------------------------------------------------
def _transition(
    session: Session,
    tenant_id,
    ticket: Ticket,
    target: str,
    *,
    event_type: str,
    payload: dict | None = None,
    actor: str = "system",
    actor_type: str = "agent",
    correlation_id: str | None = None,
    causation_id: str | None = None,
) -> Ticket:
    from ..state_machine import ticket_transition

    ticket_transition(ticket.status, target)  # raises on invalid transition
    previous = ticket.status
    ticket.status = target
    ticket.customer_status = ticket.customer_visible_status()
    if target == "RESOLVED":
        ticket.resolved_at = _now()
    elif target == "CLOSED":
        ticket.closed_at = _now()
    elif target == "REOPENED":
        ticket.reopened_at = _now()
        ticket.reopened_count += 1
    elif target == "CANCELLED":
        ticket.resolved_at = _now()
    elif target == "DUPLICATE":
        ticket.resolved_at = _now()

    # SLA pause/resume based on the policy's pause_on_states.
    _apply_sla_state_effect(session, ticket, previous, target)

    append_event(session, ticket, event_type,
                 payload={"from": previous, "to": target, **(payload or {})},
                 actor_type=actor_type, actor_id=actor,
                 correlation_id=correlation_id or ticket.correlation_id, causation_id=causation_id)
    session.flush()
    return ticket


def _apply_sla_state_effect(session: Session, ticket: Ticket, previous: str, target: str) -> None:
    sla = sla_service.get_ticket_sla(session, ticket)
    if sla is None:
        return
    pause_states = (sla.policy_snapshot.get("definition") or {}).get("pause_on_states", [])
    calendar = catalog_service.get_or_create_calendar(session, ticket.tenant_id)
    if previous not in pause_states and target in pause_states:
        if sla_engine.pause_sla(session, sla, calendar):
            append_event(session, ticket, "ticket.sla_paused",
                         payload={"from_state": previous, "to_state": target, "paused_at": _now().isoformat()},
                         actor_type="system", actor_id="sla-engine", correlation_id=ticket.correlation_id)
    elif previous in pause_states and target not in pause_states:
        if sla_engine.resume_sla(session, sla, calendar):
            append_event(session, ticket, "ticket.sla_resumed",
                         payload={"from_state": previous, "to_state": target}, actor_type="system",
                         actor_id="sla-engine", correlation_id=ticket.correlation_id)
    ticket.sla_status = sla.status
    ticket.response_deadline = sla.response_deadline
    ticket.resolution_deadline = sla.resolution_deadline


# ---------------------------------------------------------------------------
# Assignment commands
# ---------------------------------------------------------------------------
def assign(session: Session, tenant_id, ticket_id: uuid.UUID, *, agent_id: str, agent_name: str | None = None,
           actor: str = "system", reason: str | None = None, correlation_id: str | None = None) -> Ticket:
    ticket = get_ticket_or_404(session, tenant_id, ticket_id)
    if ticket.status in ("CLOSED", "CANCELLED"):
        raise StateTransitionError(f"cannot assign a {ticket.status} ticket")
    previous = ticket.assigned_agent_id
    ticket.assigned_agent_id = agent_id
    ticket.assigned_agent_name = agent_name
    target = "ASSIGNED" if ticket.status in ("NEW", "TRIAGE", "REOPENED") else ticket.status
    if target != ticket.status:
        _transition(session, tenant_id, ticket, target, event_type="ticket.assigned",
                    payload={"agent_id": agent_id, "previous_agent": previous, "reason": reason},
                    actor=actor, correlation_id=correlation_id or ticket.correlation_id)
    else:
        append_event(session, ticket, "ticket.assigned",
                     payload={"agent_id": agent_id, "previous_agent": previous, "reason": reason},
                     actor_type="agent", actor_id=actor, correlation_id=correlation_id or ticket.correlation_id)
        session.flush()
    outbox(session, "support.ticket.assigned.v1", tenant_id, correlation_id or ticket.correlation_id,
           {"ticket_id": str(ticket.id), "ticket_number": ticket.ticket_number, "agent_id": agent_id})
    return ticket


def reassign(session: Session, tenant_id, ticket_id: uuid.UUID, *, agent_id: str, agent_name: str | None = None,
             actor: str = "system", reason: str, correlation_id: str | None = None) -> Ticket:
    if not reason or not reason.strip():
        raise ValidationError("reassignment requires a reason")
    return assign(session, tenant_id, ticket_id, agent_id=agent_id, agent_name=agent_name,
                  actor=actor, reason=reason, correlation_id=correlation_id)


def transfer_queue(session: Session, tenant_id, ticket_id: uuid.UUID, *, queue_code: str,
                   actor: str = "system", reason: str | None = None, correlation_id: str | None = None) -> Ticket:
    ticket = get_ticket_or_404(session, tenant_id, ticket_id)
    if ticket.status in ("CLOSED", "CANCELLED"):
        raise StateTransitionError(f"cannot transfer a {ticket.status} ticket")
    queue = assignment_service.resolve_queue(session, tenant_id, queue_code)
    previous = str(ticket.assigned_queue_id) if ticket.assigned_queue_id else None
    ticket.assigned_queue_id = queue.id
    ticket.assigned_agent_id = None
    ticket.assigned_agent_name = None
    if ticket.status in ("NEW", "ASSIGNED", "IN_PROGRESS", "REOPENED"):
        target = "TRIAGE" if ticket.status == "REOPENED" else "ASSIGNED"
        _transition(session, tenant_id, ticket, target, event_type="ticket.queue_transferred",
                    payload={"from_queue": previous, "to_queue": queue.code, "reason": reason},
                    actor=actor, correlation_id=correlation_id or ticket.correlation_id)
    else:
        append_event(session, ticket, "ticket.queue_transferred",
                     payload={"from_queue": previous, "to_queue": queue.code, "reason": reason},
                     actor_type="agent", actor_id=actor, correlation_id=correlation_id or ticket.correlation_id)
        session.flush()
    return ticket


def accept(session: Session, tenant_id, ticket_id: uuid.UUID, *, actor: str = "system",
           correlation_id: str | None = None) -> Ticket:
    ticket = get_ticket_or_404(session, tenant_id, ticket_id)
    return _transition(session, tenant_id, ticket, "IN_PROGRESS", event_type="ticket.accepted",
                       actor=actor, correlation_id=correlation_id or ticket.correlation_id)


def start_work(session: Session, tenant_id, ticket_id: uuid.UUID, *, actor: str = "system",
               correlation_id: str | None = None) -> Ticket:
    ticket = get_ticket_or_404(session, tenant_id, ticket_id)
    return _transition(session, tenant_id, ticket, "IN_PROGRESS", event_type="ticket.work_started",
                       actor=actor, correlation_id=correlation_id or ticket.correlation_id)


def request_customer_info(session: Session, tenant_id, ticket_id: uuid.UUID, *, message: str,
                          actor: str = "system", correlation_id: str | None = None) -> Ticket:
    ticket = get_ticket_or_404(session, tenant_id, ticket_id)
    ticket = _transition(session, tenant_id, ticket, "PENDING_CUSTOMER", event_type="ticket.customer_info_requested",
                         payload={"message": message}, actor=actor, correlation_id=correlation_id or ticket.correlation_id)
    session.flush()
    return ticket


def escalate(session: Session, tenant_id, ticket_id: uuid.UUID, *, reason: str, actor: str = "system",
             correlation_id: str | None = None, trigger: str = "CUSTOMER_ESCALATION") -> Ticket:
    ticket = get_ticket_or_404(session, tenant_id, ticket_id)
    ticket = _transition(session, tenant_id, ticket, "ESCALATED", event_type="ticket.escalated",
                         payload={"reason": reason, "trigger": trigger}, actor=actor,
                         correlation_id=correlation_id or ticket.correlation_id)
    from .escalation_service import execute_escalation

    # execute_escalation sets the escalation level (max of current+1) and records
    # the escalation row, actions, event and outbox message.
    execute_escalation(session, ticket, trigger=trigger, reason=reason, actor=actor,
                       correlation_id=correlation_id or ticket.correlation_id)
    session.flush()
    return ticket


# ---------------------------------------------------------------------------
# Resolution / closure
# ---------------------------------------------------------------------------
def resolve(session: Session, tenant_id, ticket_id: uuid.UUID, *, resolution_code: str, summary: str,
            customer_explanation: str | None = None, root_cause_reference: str | None = None,
            related_article_id: uuid.UUID | None = None, actor: str = "system",
            correlation_id: str | None = None) -> Ticket:
    ticket = get_ticket_or_404(session, tenant_id, ticket_id)
    resolution_code = resolution_code.upper()
    if resolution_code not in RESOLUTION_CODES:
        raise ValidationError(f"invalid resolution code {resolution_code!r}")
    if not summary or not summary.strip():
        raise ValidationError("resolution summary is required")
    if ticket.status in ("CLOSED", "CANCELLED", "DUPLICATE"):
        raise StateTransitionError(f"cannot resolve a {ticket.status} ticket")
    existing = session.scalars(select(TicketResolution).where(TicketResolution.ticket_id == ticket.id)).first()
    if existing is None:
        resolution = TicketResolution(
            tenant_id=tenant_id, ticket_id=ticket.id, resolution_code=resolution_code, summary=summary.strip(),
            customer_explanation=customer_explanation, root_cause_reference=root_cause_reference,
            resolved_by=actor, related_article_id=related_article_id,
        )
        session.add(resolution)
        session.flush()
    ticket.resolution_code = resolution_code
    ticket.resolution_summary = summary.strip()
    ticket.resolution_explanation = customer_explanation
    ticket.root_cause_reference = root_cause_reference
    ticket = _transition(session, tenant_id, ticket, "RESOLVED", event_type="ticket.resolved",
                         payload={"resolution_code": resolution_code, "summary": summary},
                         actor=actor, correlation_id=correlation_id or ticket.correlation_id)
    # Mark SLA completed and schedule auto-close.
    sla = sla_service.get_ticket_sla(session, ticket)
    if sla is not None and sla.status != "BREACHED":
        sla.status = "COMPLETED"
        ticket.sla_status = sla.status
    from datetime import timedelta

    ticket.auto_close_at = _now() + timedelta(days=3)
    outbox(session, "support.ticket.resolved.v1", tenant_id, correlation_id or ticket.correlation_id,
           {"ticket_id": str(ticket.id), "ticket_number": ticket.ticket_number, "resolution_code": resolution_code})
    session.flush()
    return ticket


def close(session: Session, tenant_id, ticket_id: uuid.UUID, *, actor: str = "system",
          correlation_id: str | None = None, confirm: bool = False) -> Ticket:
    ticket = get_ticket_or_404(session, tenant_id, ticket_id)
    if ticket.status != "RESOLVED":
        raise StateTransitionError("only a resolved ticket can be closed")
    if not ticket.resolution_code or not ticket.resolution_summary:
        raise StateTransitionError("resolution code and summary are required before closure")
    ticket = _transition(session, tenant_id, ticket, "CLOSED", event_type="ticket.closed",
                         payload={"confirm": confirm}, actor=actor, correlation_id=correlation_id or ticket.correlation_id)
    ticket.csat_eligible = True
    sla = sla_service.get_ticket_sla(session, ticket)
    if sla is not None:
        sla.status = "COMPLETED"
        ticket.sla_status = sla.status
    outbox(session, "support.ticket.closed.v1", tenant_id, correlation_id or ticket.correlation_id,
           {"ticket_id": str(ticket.id), "ticket_number": ticket.ticket_number})
    session.flush()
    return ticket


def reopen(session: Session, tenant_id, ticket_id: uuid.UUID, *, reason: str, actor: str = "system",
           correlation_id: str | None = None) -> Ticket:
    ticket = get_ticket_or_404(session, tenant_id, ticket_id)
    if ticket.status not in ("RESOLVED", "CLOSED", "DUPLICATE"):
        raise StateTransitionError("only resolved, closed or duplicate tickets can be reopened")
    if not reason or not reason.strip():
        raise ValidationError("reopen requires a reason")
    ticket.csat_eligible = False
    ticket = _transition(session, tenant_id, ticket, "REOPENED", event_type="ticket.reopened",
                         payload={"reason": reason}, actor=actor, correlation_id=correlation_id or ticket.correlation_id)
    sla = sla_service.get_ticket_sla(session, ticket)
    if sla is not None:
        definition = sla.policy_snapshot.get("definition") or {}
        if definition.get("reopen_policy", "RESTART") == "RESTART":
            sla_engine.restart_sla_run(session, sla)
        else:
            sla.status = "ACTIVE"
        ticket.sla_status = sla.status
        ticket.response_deadline = sla.response_deadline
        ticket.resolution_deadline = sla.resolution_deadline
    outbox(session, "support.ticket.reopened.v1", tenant_id, correlation_id or ticket.correlation_id,
           {"ticket_id": str(ticket.id), "ticket_number": ticket.ticket_number, "reason": reason})
    session.flush()
    return ticket


def cancel(session: Session, tenant_id, ticket_id: uuid.UUID, *, reason: str, actor: str = "system",
           correlation_id: str | None = None) -> Ticket:
    ticket = get_ticket_or_404(session, tenant_id, ticket_id)
    if ticket.status in ("CLOSED", "CANCELLED"):
        raise StateTransitionError(f"cannot cancel a {ticket.status} ticket")
    if not reason or not reason.strip():
        raise ValidationError("cancellation requires a reason")
    ticket = _transition(session, tenant_id, ticket, "CANCELLED", event_type="ticket.cancelled",
                         payload={"reason": reason}, actor=actor, correlation_id=correlation_id or ticket.correlation_id)
    ticket.csat_eligible = False
    sla = sla_service.get_ticket_sla(session, ticket)
    if sla is not None:
        sla.status = "COMPLETED"
        ticket.sla_status = sla.status
    session.flush()
    return ticket


def mark_duplicate(session: Session, tenant_id, ticket_id: uuid.UUID, *, original_ticket_id: uuid.UUID,
                   reason: str, actor: str = "system", correlation_id: str | None = None) -> Ticket:
    ticket = get_ticket_or_404(session, tenant_id, ticket_id)
    original = get_ticket_or_404(session, tenant_id, original_ticket_id)
    if original.id == ticket.id:
        raise ValidationError("a ticket cannot be a duplicate of itself")
    if ticket.status in ("CLOSED", "CANCELLED"):
        raise StateTransitionError(f"cannot mark a {ticket.status} ticket duplicate")
    ticket = _transition(session, tenant_id, ticket, "DUPLICATE", event_type="ticket.marked_duplicate",
                         payload={"original_ticket_id": str(original.id), "original_number": original.ticket_number, "reason": reason},
                         actor=actor, correlation_id=correlation_id or ticket.correlation_id)
    session.add(TicketRelationship(tenant_id=tenant_id, from_ticket_id=ticket.id, to_ticket_id=original.id,
                                   relation_type="DUPLICATE_OF", created_by=actor))
    ticket.csat_eligible = False
    sla = sla_service.get_ticket_sla(session, ticket)
    if sla is not None:
        sla.status = "COMPLETED"
        ticket.sla_status = sla.status
    session.flush()
    return ticket


# ---------------------------------------------------------------------------
# Category / priority changes
# ---------------------------------------------------------------------------
def change_category(session: Session, tenant_id, ticket_id: uuid.UUID, *, category_code: str,
                    subcategory_code: str | None = None, actor: str = "system", reason: str | None = None,
                    correlation_id: str | None = None) -> Ticket:
    ticket = get_ticket_or_404(session, tenant_id, ticket_id)
    category = session.scalars(
        select(TicketCategory).where(
            TicketCategory.code == category_code.upper(),
            (TicketCategory.tenant_id == tenant_id) | (TicketCategory.tenant_id.is_(None)),
        )
    ).first()
    if category is None:
        raise ValidationError(f"unknown category {category_code!r}")
    subcategory = None
    if subcategory_code:
        subcategory = session.scalars(
            select(TicketSubcategory).where(
                TicketSubcategory.category_id == category.id, TicketSubcategory.code == subcategory_code.upper())
        ).first()
        if subcategory is None:
            raise ValidationError(f"unknown subcategory {subcategory_code!r}")
    previous = str(ticket.category_id)
    ticket.category_id = category.id
    ticket.subcategory_id = subcategory.id if subcategory else None
    append_event(session, ticket, "ticket.category_changed",
                 payload={"from_category": previous, "to_category": category.code, "reason": reason},
                 actor_type="agent", actor_id=actor, correlation_id=correlation_id or ticket.correlation_id)
    session.flush()
    return ticket


def change_priority(session: Session, tenant_id, ticket_id: uuid.UUID, *, priority: str, reason: str,
                    actor: str = "system", correlation_id: str | None = None) -> Ticket:
    if not reason or not reason.strip():
        raise ValidationError("priority change requires a reason")
    priority = validate_priority(priority)
    ticket = get_ticket_or_404(session, tenant_id, ticket_id)
    previous = ticket.priority
    if previous == priority:
        return ticket
    ticket.priority = priority
    ticket.severity = severity_for_priority(priority)
    # Recalculate SLA deadlines preserving the elapsed fraction.
    sla = sla_service.get_ticket_sla(session, ticket)
    if sla is not None:
        from ..models import SLAPolicy

        policy = session.get(SLAPolicy, sla.policy_id)
        if policy is not None:
            version = sla_engine.active_version(session, policy)
            sla_engine.recalculate_for_priority(session, sla, version, priority)
            ticket.sla_status = sla.status
            ticket.response_deadline = sla.response_deadline
            ticket.resolution_deadline = sla.resolution_deadline
    append_event(session, ticket, "ticket.priority_changed",
                 payload={"from": previous, "to": priority, "reason": reason},
                 actor_type="agent", actor_id=actor, correlation_id=correlation_id or ticket.correlation_id)
    outbox(session, "support.ticket.priority_changed.v1", tenant_id, correlation_id or ticket.correlation_id,
           {"ticket_id": str(ticket.id), "ticket_number": ticket.ticket_number, "from": previous, "to": priority, "reason": reason})
    session.flush()
    return ticket


# ---------------------------------------------------------------------------
# Watchers
# ---------------------------------------------------------------------------
def add_watcher(session: Session, tenant_id, ticket_id: uuid.UUID, *, watcher_type: str, watcher_id: str,
                actor: str = "system", correlation_id: str | None = None) -> Ticket:
    ticket = get_ticket_or_404(session, tenant_id, ticket_id)
    existing = session.scalars(
        select(TicketWatcher).where(TicketWatcher.ticket_id == ticket.id,
                                    TicketWatcher.watcher_type == watcher_type,
                                    TicketWatcher.watcher_id == watcher_id)
    ).first()
    if existing is None:
        session.add(TicketWatcher(tenant_id=tenant_id, ticket_id=ticket.id, watcher_type=watcher_type, watcher_id=watcher_id))
        append_event(session, ticket, "ticket.watcher_added", payload={"watcher_type": watcher_type, "watcher_id": watcher_id},
                     actor_type="agent", actor_id=actor, correlation_id=correlation_id or ticket.correlation_id)
        session.flush()
    return ticket


def remove_watcher(session: Session, tenant_id, ticket_id: uuid.UUID, *, watcher_type: str, watcher_id: str,
                   actor: str = "system", correlation_id: str | None = None) -> Ticket:
    ticket = get_ticket_or_404(session, tenant_id, ticket_id)
    watcher = session.scalars(
        select(TicketWatcher).where(TicketWatcher.ticket_id == ticket.id,
                                    TicketWatcher.watcher_type == watcher_type,
                                    TicketWatcher.watcher_id == watcher_id)
    ).first()
    if watcher is not None:
        session.delete(watcher)
        append_event(session, ticket, "ticket.watcher_removed", payload={"watcher_type": watcher_type, "watcher_id": watcher_id},
                     actor_type="agent", actor_id=actor, correlation_id=correlation_id or ticket.correlation_id)
        session.flush()
    return ticket


# ---------------------------------------------------------------------------
# Related records / outage / order / job linking
# ---------------------------------------------------------------------------
def link_related(session: Session, tenant_id, ticket_id: uuid.UUID, *, relation_type: str, to_ticket_id: uuid.UUID,
                 actor: str = "system", correlation_id: str | None = None) -> Ticket:
    ticket = get_ticket_or_404(session, tenant_id, ticket_id)
    to_ticket = get_ticket_or_404(session, tenant_id, to_ticket_id)
    existing = session.scalars(
        select(TicketRelationship).where(TicketRelationship.from_ticket_id == ticket.id,
                                         TicketRelationship.to_ticket_id == to_ticket.id,
                                         TicketRelationship.relation_type == relation_type)
    ).first()
    if existing is None:
        session.add(TicketRelationship(tenant_id=tenant_id, from_ticket_id=ticket.id, to_ticket_id=to_ticket.id,
                                       relation_type=relation_type, created_by=actor))
        append_event(session, ticket, "ticket.relationship_linked",
                     payload={"relation_type": relation_type, "to_ticket": to_ticket.ticket_number},
                     actor_type="agent", actor_id=actor, correlation_id=correlation_id or ticket.correlation_id)
        session.flush()
    return ticket


def link_outage(session: Session, tenant_id, ticket_id: uuid.UUID, *, incident_id: str, incident_number: str | None = None,
                actor: str = "system", correlation_id: str | None = None, auto: bool = False) -> Ticket:
    ticket = get_ticket_or_404(session, tenant_id, ticket_id)
    ticket.nms_incident_id = incident_id
    ticket.nms_incident_number = incident_number
    append_event(session, ticket, "ticket.outage_linked",
                 payload={"incident_id": incident_id, "incident_number": incident_number, "auto": auto},
                 actor_type="system" if auto else "agent", actor_id=actor, correlation_id=correlation_id or ticket.correlation_id)
    outbox(session, "support.ticket.outage_linked.v1", tenant_id, correlation_id or ticket.correlation_id,
           {"ticket_id": str(ticket.id), "ticket_number": ticket.ticket_number, "incident_id": incident_id})
    session.flush()
    return ticket


def unlink_outage(session: Session, tenant_id, ticket_id: uuid.UUID, *, actor: str = "system",
                  correlation_id: str | None = None) -> Ticket:
    ticket = get_ticket_or_404(session, tenant_id, ticket_id)
    previous = ticket.nms_incident_id
    ticket.nms_incident_id = None
    ticket.nms_incident_number = None
    append_event(session, ticket, "ticket.outage_unlinked", payload={"incident_id": previous},
                 actor_type="agent", actor_id=actor, correlation_id=correlation_id or ticket.correlation_id)
    session.flush()
    return ticket


def link_oss_order(session: Session, tenant_id, ticket_id: uuid.UUID, *, order_id: str, order_number: str | None = None,
                   actor: str = "system", correlation_id: str | None = None, auto: bool = False) -> Ticket:
    ticket = get_ticket_or_404(session, tenant_id, ticket_id)
    ticket.oss_order_id = order_id
    ticket.oss_order_number = order_number
    if ticket.status in ("NEW", "ASSIGNED", "IN_PROGRESS", "REOPENED"):
        _transition(session, tenant_id, ticket, "PENDING_OSS_ORDER", event_type="ticket.oss_order_linked",
                    payload={"order_id": order_id, "order_number": order_number, "auto": auto},
                    actor=actor, correlation_id=correlation_id or ticket.correlation_id)
    else:
        append_event(session, ticket, "ticket.oss_order_linked",
                     payload={"order_id": order_id, "order_number": order_number, "auto": auto},
                     actor_type="system" if auto else "agent", actor_id=actor,
                     correlation_id=correlation_id or ticket.correlation_id)
        session.flush()
    outbox(session, "support.ticket.oss_order_linked.v1", tenant_id, correlation_id or ticket.correlation_id,
           {"ticket_id": str(ticket.id), "ticket_number": ticket.ticket_number, "order_id": order_id})
    session.flush()
    return ticket


def link_workforce_job(session: Session, tenant_id, ticket_id: uuid.UUID, *, job_id: str, job_number: str | None = None,
                       actor: str = "system", correlation_id: str | None = None, auto: bool = False) -> Ticket:
    ticket = get_ticket_or_404(session, tenant_id, ticket_id)
    ticket.workforce_job_id = job_id
    ticket.workforce_job_number = job_number
    if ticket.status in ("NEW", "ASSIGNED", "IN_PROGRESS", "REOPENED"):
        _transition(session, tenant_id, ticket, "PENDING_FIELD_VISIT", event_type="ticket.field_job_created",
                    payload={"job_id": job_id, "job_number": job_number}, actor=actor,
                    correlation_id=correlation_id or ticket.correlation_id)
    append_event(session, ticket, "ticket.workforce_job_linked",
                 payload={"job_id": job_id, "job_number": job_number, "auto": auto},
                 actor_type="system" if auto else "agent", actor_id=actor, correlation_id=correlation_id or ticket.correlation_id)
    outbox(session, "support.ticket.workforce_job_linked.v1", tenant_id, correlation_id or ticket.correlation_id,
           {"ticket_id": str(ticket.id), "ticket_number": ticket.ticket_number, "job_id": job_id})
    session.flush()
    return ticket


def link_billing_dispute(session: Session, tenant_id, ticket_id: uuid.UUID, *, dispute_id: str,
                         actor: str = "system", correlation_id: str | None = None) -> Ticket:
    ticket = get_ticket_or_404(session, tenant_id, ticket_id)
    ticket.billing_dispute_id = dispute_id
    append_event(session, ticket, "ticket.billing_dispute_linked", payload={"dispute_id": dispute_id},
                 actor_type="agent", actor_id=actor, correlation_id=correlation_id or ticket.correlation_id)
    session.flush()
    return ticket


# ---------------------------------------------------------------------------
# CSAT
# ---------------------------------------------------------------------------
def submit_csat(session: Session, tenant_id, ticket_id: uuid.UUID, *, rating: int, comment: str | None = None,
                channel: str = "CUSTOMER_PORTAL", correlation_id: str | None = None) -> dict:
    ticket = get_ticket_or_404(session, tenant_id, ticket_id)
    return csat_service.submit_csat(session, tenant_id, ticket, rating=rating, comment=comment, channel=channel,
                                    correlation_id=correlation_id or ticket.correlation_id)


def record_satisfaction_response(ticket: Ticket) -> None:
    """Compatibility helper — satisfaction is recorded via submit_csat."""
    return None


def add_outage_clear_marker(ticket: Ticket, session: Session) -> None:
    """Append an immutable marker that an associated outage cleared and service
    restoration still needs verification. Never auto-closes the ticket."""
    append_event(session, ticket, "ticket.outage_cleared_verification_pending",
                 payload={"incident_id": ticket.nms_incident_id, "message": "outage cleared; verify service restoration"},
                 actor_type="system", actor_id="outage-consumer", correlation_id=ticket.correlation_id)
    session.flush()
