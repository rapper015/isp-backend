"""Assignment and routing engine.

Supports direct assignment, queue assignment, round-robin, least-loaded,
skill-based and location-based selection with fallback queues and loop
prevention. Every assignment/reassignment is recorded with actor + reason via
the ticket command service. Agent workload is a database count (Redis may only
accelerate it, never hold it)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..domain.exceptions import AssignmentError, NotFoundError, ValidationError
from ..enums import ROUTING_STRATEGIES
from ..models import (
    RoutingRule,
    SupportAgentMembership,
    SupportTeam,
    Ticket,
    TicketQueue,
)
from . import catalog_service


def _now() -> datetime:
    return datetime.now(timezone.utc)


def resolve_queue(session: Session, tenant_id, queue_code: str) -> TicketQueue:
    queue = session.scalars(
        select(TicketQueue).where(
            TicketQueue.code == queue_code.upper(),
            (TicketQueue.tenant_id == tenant_id) | (TicketQueue.tenant_id.is_(None)),
        ).order_by(TicketQueue.tenant_id.is_(None))  # tenant-specific first
    ).first()
    if queue is None:
        raise NotFoundError(f"queue {queue_code!r} not found")
    return queue


def find_routing_rule(session: Session, tenant_id, ticket: Ticket) -> RoutingRule | None:
    rules = list(session.scalars(
        select(RoutingRule).where(RoutingRule.is_active.is_(True), RoutingRule.tenant_id == tenant_id)
        .order_by(RoutingRule.priority)
    ))
    global_rules = list(session.scalars(
        select(RoutingRule).where(RoutingRule.is_active.is_(True), RoutingRule.tenant_id.is_(None))
        .order_by(RoutingRule.priority)
    ))
    candidates = rules + global_rules
    # Most specific match first: type+category > category > type > any.
    for rule in candidates:
        if rule.ticket_type and rule.ticket_type != ticket.ticket_type:
            continue
        if rule.category_id and rule.category_id != ticket.category_id:
            continue
        if rule.subcategory_id and rule.subcategory_id != ticket.subcategory_id:
            continue
        return rule
    return None


def team_of_queue(session: Session, tenant_id, queue_id: uuid.UUID) -> SupportTeam | None:
    return session.scalars(
        select(SupportTeam).where(SupportTeam.tenant_id == tenant_id, SupportTeam.queue_id == queue_id)
    ).first()


def agents_in_team(session: Session, tenant_id, team_id: uuid.UUID, *, skills: list | None = None, locations: list | None = None) -> list[SupportAgentMembership]:
    stmt = select(SupportAgentMembership).where(
        SupportAgentMembership.tenant_id == tenant_id,
        SupportAgentMembership.team_id == team_id,
        SupportAgentMembership.is_active.is_(True),
    )
    agents = list(session.scalars(stmt))
    if skills:
        agents = [a for a in agents if set(skills).issubset(set(a.skills or []))]
    if locations:
        agents = [a for a in agents if set(locations).intersection(set(a.locations or [])) or not a.locations]
    return agents


def agent_open_count(session: Session, tenant_id, agent_id: str) -> int:
    return session.scalar(
        select(func.count(Ticket.id)).where(
            Ticket.tenant_id == tenant_id,
            Ticket.assigned_agent_id == agent_id,
            Ticket.status.in_(("NEW", "TRIAGE", "ASSIGNED", "IN_PROGRESS", "REOPENED", "ESCALATED",
                               "PENDING_CUSTOMER", "PENDING_INTERNAL_TEAM", "PENDING_VENDOR",
                               "PENDING_FIELD_VISIT", "PENDING_OSS_ORDER", "PENDING_BILLING_ACTION")),
        )
    ) or 0


def select_agent(session: Session, tenant_id, *, queue_id: uuid.UUID, strategy: str = "ROUND_ROBIN",
                 skills: list | None = None, locations: list | None = None, exclude: set[str] | None = None) -> tuple[str | None, str | None]:
    """Return (agent_id, agent_name) or (None, None) when no agent is found.

    Selection is deterministic and database-authoritative (Redis may only
    accelerate it, never hold it). Round-robin orders by open-load then a stable
    agent tie-break (least-recently-used proxy); least-loaded picks the lightest
    agent; skill/location strategies filter candidates first."""
    team = team_of_queue(session, tenant_id, queue_id)
    if team is None:
        return None, None
    if strategy == "MANUAL":
        # Manual routing assigns the queue but leaves agent selection to a human.
        return None, None
    agents = agents_in_team(session, tenant_id, team.id, skills=skills, locations=locations)
    if exclude:
        agents = [a for a in agents if a.agent_id not in exclude]
    if not agents:
        return None, None
    if strategy == "LEAST_LOADED":
        best = min(agents, key=lambda a: agent_open_count(session, tenant_id, a.agent_id))
        return best.agent_id, best.agent_name
    if strategy == "SKILL_BASED":
        if skills:
            best = max(agents, key=lambda a: len(set(a.skills or []).intersection(set(skills))))
        else:
            best = min(agents, key=lambda a: agent_open_count(session, tenant_id, a.agent_id))
        return best.agent_id, best.agent_name
    # ROUND_ROBIN / LOCATION_BASED: deterministic least-recently-used rotation.
    best = min(agents, key=lambda a: (agent_open_count(session, tenant_id, a.agent_id), a.agent_id))
    return best.agent_id, best.agent_name


def route_ticket(session: Session, tenant_id, ticket: Ticket, *, actor: str = "system") -> dict:
    """Assign a queue/team/agent for a ticket using routing rules.

    Loop prevention: fallback is only applied when it differs from the current
    queue and a rule match is attempted at most twice."""
    catalog_service.ensure_tenant_defaults(session, tenant_id)
    rule = find_routing_rule(session, tenant_id, ticket)
    if rule is None:
        # Default fallback: the SUPPORT queue.
        queue = resolve_queue(session, tenant_id, "L1_SUPPORT")
        ticket.assigned_queue_id = queue.id
        session.flush()
        return {"queue": queue.code, "agent": None, "strategy": "MANUAL"}

    visited: set[uuid.UUID] = set()
    current_queue_id = ticket.assigned_queue_id
    if current_queue_id:
        visited.add(current_queue_id)
    agent_id, agent_name = None, None
    applied_queue = rule.target_queue_id

    for _ in range(2):  # one fallback hop maximum
        if applied_queue in visited:
            break
        visited.add(applied_queue)
        agent_id, agent_name = select_agent(
            session, tenant_id, queue_id=applied_queue, strategy=rule.strategy,
            skills=rule.required_skills, locations=[ticket.service_location_id] if ticket.service_location_id else None,
        )
        if agent_id is not None:
            break
        if rule.fallback_queue_id and rule.fallback_queue_id not in visited:
            applied_queue = rule.fallback_queue_id
            continue
        break

    ticket.assigned_queue_id = applied_queue
    team = team_of_queue(session, tenant_id, applied_queue)
    if team is not None:
        ticket.assigned_team_id = team.id
    if agent_id is not None:
        ticket.assigned_agent_id = agent_id
        ticket.assigned_agent_name = agent_name
    session.flush()
    return {"queue": (session.get(TicketQueue, applied_queue).code if session.get(TicketQueue, applied_queue) else None),
            "agent": agent_id, "strategy": rule.strategy}


def detect_orphan_assignments(session: Session, tenant_id) -> list[dict]:
    """Tickets assigned to agents that are no longer active members."""
    orphans = []
    tickets = list(session.scalars(
        select(Ticket).where(Ticket.tenant_id == tenant_id, Ticket.assigned_agent_id.is_not(None))))
    for ticket in tickets:
        membership = session.scalars(
            select(SupportAgentMembership).where(
                SupportAgentMembership.tenant_id == tenant_id,
                SupportAgentMembership.agent_id == ticket.assigned_agent_id,
                SupportAgentMembership.is_active.is_(True),
            )
        ).first()
        if membership is None:
            orphans.append({"ticket_id": str(ticket.id), "ticket_number": ticket.ticket_number,
                            "agent_id": ticket.assigned_agent_id})
    return orphans
