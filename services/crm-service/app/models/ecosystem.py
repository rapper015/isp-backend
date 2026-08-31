"""CRM partner, ecosystem, SLA/automation models (Master Spec Batch 6).

Covers: 310 SLA timers, 391 reseller regulatory tracking, 392 ticket
escalation, 400 partner hierarchy scaling, 823 partner performance, 825 partner
SLA management, 826 cross-operator federation, 1191 suggested resolutions.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Timestamped
from ..database import Base


class Partner(Base, Timestamped):
    __tablename__ = "crm_partners"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_crm_partner_code"),
                      Index("ix_crm_partner_tenant", "tenant_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True, nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    partner_type: Mapped[str] = mapped_column(String(40), default="OPERATOR")
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    sla_minutes: Mapped[int] = mapped_column(Integer, default=240)
    sla_pct: Mapped[float] = mapped_column(Float, default=100.0)
    performance_score: Mapped[float] = mapped_column(Float, default=100.0)
    breaches: Mapped[int] = mapped_column(Integer, default=0)


class PartnerPerformanceRecord(Base, Timestamped):
    __tablename__ = "crm_partner_performance"
    __table_args__ = (UniqueConstraint("tenant_id", "partner_id", "period", name="uq_crm_partner_perf"),
                      Index("ix_crm_partner_perf_tenant", "tenant_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True, nullable=False)
    partner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crm_partners.id"), nullable=False)
    period: Mapped[str] = mapped_column(String(20), default="MONTH")
    kpi: Mapped[dict] = mapped_column(JSON, default=dict)  # e.g. {"orders": 120, "conversions": 24}


class PartnerHierarchyNode(Base, Timestamped):
    __tablename__ = "crm_partner_hierarchy"
    __table_args__ = (UniqueConstraint("tenant_id", "partner_id", name="uq_crm_partner_hierarchy"),
                      Index("ix_crm_partner_hier_tenant", "tenant_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True, nullable=False)
    partner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crm_partners.id"), nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("crm_partners.id"), nullable=True)
    level: Mapped[int] = mapped_column(Integer, default=1)


class FederationLink(Base, Timestamped):
    __tablename__ = "crm_federation_links"
    __table_args__ = (UniqueConstraint("tenant_id", "operator_name", name="uq_crm_federation_operator"),
                      Index("ix_crm_federation_tenant", "tenant_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True, nullable=False)
    operator_name: Mapped[str] = mapped_column(String(200), nullable=False)
    direction: Mapped[str] = mapped_column(String(20), default="OUTBOUND")  # OUTBOUND | INBOUND | BIDIRECTIONAL
    protocol: Mapped[str] = mapped_column(String(40), default="API")
    status: Mapped[str] = mapped_column(String(20), default="LINKED")


class TicketSlaTimer(Base, Timestamped):
    __tablename__ = "crm_ticket_sla"
    __table_args__ = (UniqueConstraint("tenant_id", "ticket_id", name="uq_crm_ticket_sla"),
                      Index("ix_crm_ticket_sla_tenant", "tenant_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True, nullable=False)
    ticket_id: Mapped[str] = mapped_column(String(128), nullable=False)
    sla_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    breached: Mapped[bool] = mapped_column(default=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TicketEscalation(Base, Timestamped):
    __tablename__ = "crm_ticket_escalations"
    __table_args__ = (Index("ix_crm_ticket_esc_tenant", "tenant_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True, nullable=False)
    ticket_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    level: Mapped[str] = mapped_column(String(20), default="LEVEL_1")
    reason: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="OPEN")
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TicketSuggestion(Base, Timestamped):
    __tablename__ = "crm_ticket_suggestions"
    __table_args__ = (Index("ix_crm_ticket_sugg_tenant", "tenant_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True, nullable=False)
    ticket_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    suggestion: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(30), default="AI")
    confidence: Mapped[float] = mapped_column(Float, default=0.5)


class ResellerRegulatoryRecord(Base, Timestamped):
    __tablename__ = "crm_reseller_regulatory"
    __table_args__ = (UniqueConstraint("tenant_id", "reseller_id", "report_type", name="uq_crm_regulatory"),
                      Index("ix_crm_regulatory_tenant", "tenant_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True, nullable=False)
    reseller_id: Mapped[str] = mapped_column(String(128), nullable=False)
    report_type: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="TRACKED")
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class KbFeedback(Base, Timestamped):
    """KB feedback loop (feature 1190)."""
    __tablename__ = "crm_kb_feedback"
    __table_args__ = (Index("ix_crm_kb_feedback_tenant", "tenant_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True, nullable=False)
    article_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    rating: Mapped[int] = mapped_column(Integer, default=0)  # 1..5
    helpful: Mapped[bool] = mapped_column(default=False)
    feedback: Mapped[str | None] = mapped_column(Text)
    applied: Mapped[bool] = mapped_column(default=False)  # consumed to improve KB


class ExperienceRecovery(Base, Timestamped):
    """Experience recovery engine (feature 1459): auto-recover degraded QoE."""
    __tablename__ = "crm_experience_recovery"
    __table_args__ = (Index("ix_crm_recovery_tenant", "tenant_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True, nullable=False)
    customer_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    metric: Mapped[str] = mapped_column(String(80), nullable=False)  # qoe, latency, mos
    degraded_value: Mapped[float] = mapped_column(Float, default=0.0)
    threshold: Mapped[float] = mapped_column(Float, default=0.0)
    recovery_action: Mapped[str] = mapped_column(String(200), default="NOTIFY")
    status: Mapped[str] = mapped_column(String(20), default="TRIGGERED")


class LoyaltyScore(Base, Timestamped):
    """Behavioral loyalty scoring (feature 1460)."""
    __tablename__ = "crm_loyalty_score"
    __table_args__ = (UniqueConstraint("tenant_id", "customer_id", "period", name="uq_crm_loyalty"),
                      Index("ix_crm_loyalty_tenant", "tenant_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True, nullable=False)
    customer_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    period: Mapped[str] = mapped_column(String(20), default="MONTH")
    score: Mapped[float] = mapped_column(Float, default=0.0)
    behavioral_factors: Mapped[dict] = mapped_column(JSON, default=dict)  # engagement, advocacy, tenure...
