"""Support configuration catalogue: ticket types, categories, subcategories,
queues, teams, agent memberships, routing rules, SLA policies (versioned),
business calendars, holidays and knowledge articles.

`tenant_id` is NULL for the platform-provided global defaults; records are
resolved as `tenant-specific OR global` at runtime. Versioned SLA definitions
are immutable once published (see SLAPolicyVersion).
"""
import uuid
from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Timestamped
from ..database import Base


class TicketType(Base, Timestamped):
    __tablename__ = "support_ticket_types"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_support_ticket_type_tenant_code"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    requires_service_order: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class TicketCategory(Base, Timestamped):
    __tablename__ = "support_ticket_categories"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_support_ticket_category_tenant_code"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class TicketSubcategory(Base, Timestamped):
    __tablename__ = "support_ticket_subcategories"
    __table_args__ = (UniqueConstraint("category_id", "code", name="uq_support_ticket_subcategory_category_code"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    category_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("support_ticket_categories.id"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class TicketQueue(Base, Timestamped):
    __tablename__ = "support_queues"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_support_queue_tenant_code"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    queue_type: Mapped[str] = mapped_column(String(32), default="SUPPORT", nullable=False)  # SUPPORT / APP_SUPPORT / BILLING / NOC / FIELD
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class SupportTeam(Base, Timestamped):
    __tablename__ = "support_teams"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_support_team_tenant_code"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    queue_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("support_queues.id"), nullable=True, index=True)
    team_lead_agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class SupportAgentMembership(Base, Timestamped):
    __tablename__ = "support_agent_memberships"
    __table_args__ = (
        UniqueConstraint("tenant_id", "team_id", "agent_id", name="uq_support_agent_membership"),
        Index("ix_support_agent_membership_agent", "agent_id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("support_teams.id"), nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(String(64), nullable=False)
    agent_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    role: Mapped[str] = mapped_column(String(32), default="AGENT", nullable=False)  # AGENT / TEAM_LEAD / SUPERVISOR
    skills: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    locations: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class RoutingRule(Base, Timestamped):
    __tablename__ = "support_routing_rules"
    __table_args__ = (Index("ix_support_routing_rule_tenant_active", "tenant_id", "is_active"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    ticket_type: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    category_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("support_ticket_categories.id"), nullable=True, index=True)
    subcategory_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("support_ticket_subcategories.id"), nullable=True, index=True)
    target_queue_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("support_queues.id"), nullable=False, index=True)
    strategy: Mapped[str] = mapped_column(String(24), default="ROUND_ROBIN", nullable=False)
    fallback_queue_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("support_queues.id"), nullable=True)
    required_skills: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class BusinessCalendar(Base, Timestamped):
    __tablename__ = "support_calendars"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_support_calendar_tenant_code"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)
    # working_hours: {"mon": [["09:00","18:00"]], ...} keys = mon..sun
    working_hours: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Holiday(Base, Timestamped):
    __tablename__ = "support_holidays"
    __table_args__ = (UniqueConstraint("calendar_id", "holiday_date", name="uq_support_holiday_calendar_date"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    calendar_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("support_calendars.id"), nullable=False, index=True)
    holiday_date: Mapped[date] = mapped_column(Date, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)


class SLAPolicy(Base, Timestamped):
    __tablename__ = "support_sla_policies"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_support_sla_policy_tenant_code"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    current_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class SLAPolicyVersion(Base, Timestamped):
    """Immutable published version of an SLA policy. Definition is snapshotted
    into every TicketSLA at instantiation time; later edits never rewrite
    historical deadlines."""
    __tablename__ = "support_sla_policy_versions"
    __table_args__ = (UniqueConstraint("policy_id", "version", name="uq_support_sla_policy_version"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    policy_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("support_sla_policies.id"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    activated_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # definition:
    #  {
    #    "pause_on_states": ["PENDING_CUSTOMER"],
    #    "reopen_policy": "RESTART" | "CONTINUE",
    #    "reset_on_reassign": false,
    #    "acknowledgement_counts_as_first_response": false,
    #    "escalation": [{"target": "RESOLUTION", "at_risk_pct": 75, "level": 1, "action": "NOTIFY_TEAM_LEAD"}],
    #  }
    definition: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class SLATarget(Base, Timestamped):
    """Normalized per-priority SLA targets (business seconds) for a published
    version. The TicketSLA stores an immutable snapshot of the resolved target."""
    __tablename__ = "support_sla_targets"
    __table_args__ = (UniqueConstraint("version_id", "priority", "kind", name="uq_support_sla_target"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("support_sla_policy_versions.id"), nullable=False, index=True)
    priority: Mapped[str] = mapped_column(String(16), nullable=False)  # P1_CRITICAL..P4_LOW (ALL = every priority)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # RESPONSE / RESOLUTION / ...
    business_seconds: Mapped[int] = mapped_column(Integer, nullable=False)


class KnowledgeArticle(Base, Timestamped):
    __tablename__ = "support_knowledge_articles"
    __table_args__ = (Index("ix_support_kb_tenant_active", "tenant_id", "status"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    slug: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    category_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("support_ticket_categories.id"), nullable=True, index=True)
    visibility: Mapped[str] = mapped_column(String(16), default="INTERNAL", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="DRAFT", nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    author: Mapped[str | None] = mapped_column(String(128), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tags: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    usage_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class KnowledgeUsage(Base):
    __tablename__ = "support_knowledge_usage"
    __table_args__ = (Index("ix_support_kb_usage_article", "article_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    article_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("support_knowledge_articles.id"), nullable=False)
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    used_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
