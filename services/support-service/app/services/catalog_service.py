"""Catalogue service: tenant configuration for ticket types, categories,
subcategories, queues, teams, agents, routing rules and SLA policies.

A platform-provided global default catalogue (tenant_id = NULL) is created on
startup so a fresh tenant has a working, governed support configuration. Each
tenant can override with tenant-specific records; runtime resolution prefers
tenant-specific over global."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..enums import TICKET_TYPES
from ..models import (
    BusinessCalendar,
    Holiday,
    RoutingRule,
    SLAPolicy,
    SLAPolicyVersion,
    SLATarget,
    SupportAgentMembership,
    SupportTeam,
    TicketCategory,
    TicketQueue,
    TicketSubcategory,
    TicketType,
)
from ..services.audit_service import audit, correlation
from ..domain.sla.calendar import default_working_hours

_DEFAULT_TYPES = TICKET_TYPES
_DEFAULT_CATEGORIES = {
    "CONNECTIVITY": "Connectivity",
    "SPEED": "Speed",
    "AUTHENTICATION": "Authentication",
    "DEVICE": "Device",
    "BILLING": "Billing",
    "PROVISIONING": "Provisioning",
    "APPLICATION": "Application",
    "OTHER": "Other",
}
_DEFAULT_SUBCATEGORIES = {
    "CONNECTIVITY": [("NO_INTERNET", "No internet"), ("INTERMITTENT", "Intermittent connection"),
                     ("HIGH_LATENCY", "High latency"), ("PACKET_LOSS", "Packet loss")],
    "SPEED": [("LOW_DOWNLOAD", "Low download speed"), ("LOW_UPLOAD", "Low upload speed"), ("FUP_QUERY", "FUP query")],
    "AUTHENTICATION": [("PPPOE_LOGIN_FAILURE", "PPPoE login failure"), ("HOTSPOT_LOGIN_FAILURE", "Hotspot login failure"),
                       ("MAC_BINDING_ISSUE", "MAC-binding issue")],
    "DEVICE": [("ROUTER_ISSUE", "Router issue"), ("ONT_ISSUE", "ONT/ONU issue"), ("POWER_LOS_ISSUE", "Power/LOS issue")],
    "BILLING": [("INCORRECT_INVOICE", "Incorrect invoice"), ("PAYMENT_NOT_REFLECTED", "Payment not reflected"),
                ("REFUND", "Refund"), ("PLAN_CHARGE", "Plan charge")],
    "PROVISIONING": [("NEW_CONNECTION_DELAY", "New connection delay"), ("UPGRADE_DELAY", "Upgrade delay"),
                     ("RELOCATION_DELAY", "Relocation delay")],
    "APPLICATION": [("PORTAL", "Portal"), ("MOBILE_APP", "Mobile application"), ("LOGIN", "Login")],
    "OTHER": [("OTHER", "Other")],
}
_DEFAULT_QUEUES = {
    "L1_SUPPORT": ("L1 Support", "SUPPORT"),
    "L2_SUPPORT": ("L2 Support", "SUPPORT"),
    "NOC": ("Network Operations", "NOC"),
    "BILLING": ("Billing", "BILLING"),
    "FIELD_OPS": ("Field Operations", "FIELD"),
    "APP_SUPPORT": ("Application Support", "APP_SUPPORT"),
}
_DEFAULT_TEAMS = {
    "L1_TEAM": ("L1 Team", "L1_SUPPORT"),
    "L2_TEAM": ("L2 Team", "L2_SUPPORT"),
    "NOC_TEAM": ("NOC Team", "NOC"),
    "BILLING_TEAM": ("Billing Team", "BILLING"),
    "FIELD_TEAM": ("Field Team", "FIELD_OPS"),
    "APP_TEAM": ("App Support Team", "APP_SUPPORT"),
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_global_defaults(session: Session) -> None:
    """Create the platform-wide default catalogue once (tenant_id = NULL)."""
    if session.scalars(select(TicketType).limit(1)).first() is None:
        for code in _DEFAULT_TYPES:
            session.add(TicketType(code=code, name=code.replace("_", " ").title(), is_active=True, sort_order=0))
    if session.scalars(select(TicketCategory).limit(1)).first() is None:
        for code, name in _DEFAULT_CATEGORIES.items():
            cat = TicketCategory(code=code, name=name, is_active=True, sort_order=0)
            session.add(cat)
            session.flush()
            for sub_code, sub_name in _DEFAULT_SUBCATEGORIES.get(code, []):
                session.add(TicketSubcategory(tenant_id=None, category_id=cat.id, code=sub_code, name=sub_name, is_active=True))
    if session.scalars(select(TicketQueue).limit(1)).first() is None:
        for code, (name, qtype) in _DEFAULT_QUEUES.items():
            session.add(TicketQueue(code=code, name=name, queue_type=qtype, is_active=True))
        session.flush()  # visible to the team-seeding query below (autoflush=False)
    if session.scalars(select(SupportTeam).limit(1)).first() is None:
        queues = {q.code: q for q in session.scalars(select(TicketQueue))}
        for code, (name, queue_code) in _DEFAULT_TEAMS.items():
            session.add(SupportTeam(code=code, name=name, queue_id=queues[queue_code].id, is_active=True))
    if session.scalars(select(BusinessCalendar).limit(1)).first() is None:
        session.add(BusinessCalendar(code="DEFAULT", name="Default Business Hours", timezone="UTC",
                                     working_hours=default_working_hours(), is_active=True))
    if session.scalars(select(SLAPolicy).limit(1)).first() is None:
        _create_default_sla_policy(session)
    session.flush()


def _create_default_sla_policy(session: Session) -> None:
    policy = SLAPolicy(code="DEFAULT", name="Default SLA", is_active=True, current_version=1)
    session.add(policy)
    session.flush()
    version = SLAPolicyVersion(
        tenant_id=None,
        policy_id=policy.id,
        version=1,
        is_active=True,
        activated_at=_now(),
        definition={
            "pause_on_states": ["PENDING_CUSTOMER"],
            "reopen_policy": "RESTART",
            "reset_on_reassign": False,
            "acknowledgement_counts_as_first_response": False,
            "escalation": [
                {"target": "RESPONSE", "at_risk_pct": 75, "level": 1, "action": "NOTIFY_AGENT"},
                {"target": "RESOLUTION", "at_risk_pct": 75, "level": 1, "action": "NOTIFY_TEAM_LEAD"},
                {"target": "RESOLUTION", "at_risk_pct": 90, "level": 2, "action": "ADD_SUPERVISOR_WATCHER"},
            ],
        },
    )
    session.add(version)
    session.flush()
    session.add(SLATarget(tenant_id=None, version_id=version.id, priority="ALL", kind="RESPONSE", business_seconds=4 * 3600))
    session.add(SLATarget(tenant_id=None, version_id=version.id, priority="ALL", kind="RESOLUTION", business_seconds=8 * 3600))
    session.add(SLATarget(tenant_id=None, version_id=version.id, priority="P1_CRITICAL", kind="RESPONSE", business_seconds=15 * 60))
    session.add(SLATarget(tenant_id=None, version_id=version.id, priority="P1_CRITICAL", kind="RESOLUTION", business_seconds=2 * 3600))
    session.add(SLATarget(tenant_id=None, version_id=version.id, priority="P2_HIGH", kind="RESPONSE", business_seconds=30 * 60))
    session.add(SLATarget(tenant_id=None, version_id=version.id, priority="P2_HIGH", kind="RESOLUTION", business_seconds=4 * 3600))


def ensure_tenant_defaults(session: Session, tenant_id) -> None:
    """Resolve global defaults for a tenant: create tenant-copy queue/team
    records and a tenant-specific SLA policy so tenant isolation holds."""
    ensure_global_defaults(session)
    existing = session.scalars(select(TicketQueue).where(TicketQueue.tenant_id == tenant_id)).first()
    if existing is not None:
        session.flush()
        return
    queues = {q.code: q for q in session.scalars(select(TicketQueue).where(TicketQueue.tenant_id.is_(None)))}
    for code, q in queues.items():
        session.add(TicketQueue(tenant_id=tenant_id, code=code, name=q.name, queue_type=q.queue_type, is_active=True))
    session.flush()  # visible to the tenant-queue query below (autoflush=False)
    teams = {t.code: t for t in session.scalars(select(SupportTeam).where(SupportTeam.tenant_id.is_(None)))}
    tenant_queues = {tq.code: tq for tq in session.scalars(select(TicketQueue).where(TicketQueue.tenant_id == tenant_id))}
    for code, team in teams.items():
        global_queue = session.get(TicketQueue, team.queue_id) if team.queue_id else None
        queue_id = tenant_queues[global_queue.code].id if (global_queue and global_queue.code in tenant_queues) else None
        session.add(SupportTeam(tenant_id=tenant_id, code=code, name=team.name,
                                queue_id=queue_id, is_active=True))
    session.flush()


def get_or_create_calendar(session: Session, tenant_id, code: str = "DEFAULT") -> BusinessCalendar:
    calendar = session.scalars(
        select(BusinessCalendar).where(BusinessCalendar.tenant_id == tenant_id, BusinessCalendar.code == code)
    ).first()
    if calendar is None:
        global_cal = session.scalars(
            select(BusinessCalendar).where(BusinessCalendar.tenant_id.is_(None), BusinessCalendar.code == code)
        ).first()
        calendar = BusinessCalendar(
            tenant_id=tenant_id, code=code, name=global_cal.name if global_cal else "Default Business Hours",
            timezone=global_cal.timezone if global_cal else "UTC",
            working_hours=(global_cal.working_hours if global_cal else default_working_hours()),
            is_active=True,
        )
        session.add(calendar)
        session.flush()
    return calendar


# ---------------------------------------------------------------------------
# Agent / routing management
# ---------------------------------------------------------------------------
def add_agent(session: Session, tenant_id, team_code: str, agent_id: str, *, name: str | None = None,
              role: str = "AGENT", skills: list | None = None, locations: list | None = None,
              actor: str | None = None) -> SupportAgentMembership:
    team = session.scalars(select(SupportTeam).where(SupportTeam.tenant_id == tenant_id, SupportTeam.code == team_code)).first()
    if team is None:
        raise ValueError(f"team {team_code!r} not found for tenant")
    existing = session.scalars(
        select(SupportAgentMembership).where(
            SupportAgentMembership.tenant_id == tenant_id,
            SupportAgentMembership.team_id == team.id,
            SupportAgentMembership.agent_id == agent_id,
        )
    ).first()
    if existing is not None:
        existing.is_active = True
        existing.skills = skills or []
        existing.locations = locations or []
        return existing
    membership = SupportAgentMembership(
        tenant_id=tenant_id, team_id=team.id, agent_id=agent_id, agent_name=name,
        role=role, skills=skills or [], locations=locations or [], is_active=True,
    )
    session.add(membership)
    session.flush()
    audit(session, tenant_id, "support.agent.added", "agent", agent_id, actor=actor,
          correlation_id=correlation(None), safe_after={"team": team_code, "role": role})
    return membership


def add_routing_rule(session: Session, tenant_id, *, name: str, target_queue_code: str, ticket_type: str | None = None,
                     category_code: str | None = None, strategy: str = "ROUND_ROBIN", fallback_queue_code: str | None = None,
                     required_skills: list | None = None, priority: int = 100, actor: str | None = None) -> RoutingRule:
    ensure_tenant_defaults(session, tenant_id)
    queues: dict[str, TicketQueue] = {}
    for q in session.scalars(select(TicketQueue).where(
            (TicketQueue.tenant_id == tenant_id) | (TicketQueue.tenant_id.is_(None)))):
        # Tenant-specific rows always win over global defaults (order-independent).
        if q.code not in queues or q.tenant_id is not None:
            queues[q.code] = q
    if target_queue_code not in queues:
        raise ValueError(f"target queue {target_queue_code!r} not found")
    category = None
    if category_code:
        category = session.scalars(select(TicketCategory).where(
            TicketCategory.code == category_code,
            (TicketCategory.tenant_id == tenant_id) | (TicketCategory.tenant_id.is_(None)),
        )).first()
        if category is None:
            raise ValueError(f"category {category_code!r} not found")
    rule = RoutingRule(
        tenant_id=tenant_id, name=name, ticket_type=ticket_type,
        category_id=category.id if category else None, target_queue_id=queues[target_queue_code].id,
        strategy=strategy,
        fallback_queue_id=queues[fallback_queue_code].id if fallback_queue_code else None,
        required_skills=required_skills or [], priority=priority, is_active=True,
    )
    session.add(rule)
    session.flush()
    audit(session, tenant_id, "support.routing_rule.added", "routing_rule", str(rule.id), actor=actor,
          correlation_id=correlation(None), safe_after={"name": name, "queue": target_queue_code, "strategy": strategy})
    return rule
