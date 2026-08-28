"""Lead pipeline persistence: leads, assignments, interactions, follow-ups and
stage history. All records are tenant-scoped."""
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (JSON, Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, func)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Timestamped
from ..database import Base


class Lead(Base, Timestamped):
    __tablename__ = "crm_leads"
    __table_args__ = (
        UniqueConstraint("tenant_id", "lead_number", name="uq_crm_lead_tenant_number"),
        Index("ix_crm_lead_tenant_stage", "tenant_id", "stage"),
        Index("ix_crm_lead_tenant_mobile", "tenant_id", "primary_mobile"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crm_tenants.id"), index=True, nullable=False)
    lead_number: Mapped[str] = mapped_column(String(64), nullable=False)
    lead_type: Mapped[str] = mapped_column(String(16), default="INDIVIDUAL", nullable=False)
    first_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    company_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    primary_mobile: Mapped[str] = mapped_column(String(32), nullable=False)
    alternate_mobile: Mapped[str | None] = mapped_column(String(32), nullable=True)
    primary_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    alternate_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    preferred_channel: Mapped[str | None] = mapped_column(String(32), nullable=True)
    requested_service: Mapped[str | None] = mapped_column(String(64), nullable=True)
    requested_plan_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    expected_monthly_value: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    installation_address_draft: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)
    lead_source: Mapped[str] = mapped_column(String(32), nullable=False)
    campaign_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    referrer: Mapped[str | None] = mapped_column(String(255), nullable=True)
    franchise_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("crm_franchises.id"), nullable=True, index=True)
    branch_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("crm_branches.id"), nullable=True, index=True)
    area: Mapped[str | None] = mapped_column(String(128), nullable=True)
    assigned_salesperson_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    assigned_team_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    priority: Mapped[str] = mapped_column(String(16), default="MEDIUM", nullable=False)
    stage: Mapped[str] = mapped_column(String(24), default="NEW", nullable=False, index=True)
    qualification_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    feasibility_state: Mapped[str] = mapped_column(String(24), default="UNKNOWN", nullable=False)
    feasibility_external_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lost_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    disqualification_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    next_followup_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    sla_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    converted_customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("crm_customers.id"), nullable=True, index=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class LeadAssignment(Base, Timestamped):
    __tablename__ = "crm_lead_assignments"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crm_tenants.id"), index=True, nullable=False)
    lead_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crm_leads.id"), index=True, nullable=False)
    assigned_to: Mapped[str | None] = mapped_column(String(64), nullable=True)
    assigned_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    method: Mapped[str] = mapped_column(String(24), default="MANUAL", nullable=False)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)


class LeadInteraction(Base, Timestamped):
    __tablename__ = "crm_lead_interactions"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crm_tenants.id"), index=True, nullable=False)
    lead_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("crm_leads.id"), nullable=True, index=True)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("crm_customers.id"), nullable=True, index=True)
    actor: Mapped[str | None] = mapped_column(String(128), nullable=True)
    direction: Mapped[str] = mapped_column(String(16), default="INBOUND", nullable=False)
    channel: Mapped[str] = mapped_column(String(24), nullable=False)
    subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    safe_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(255), nullable=True)
    next_action: Mapped[str | None] = mapped_column(String(500), nullable=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="COMPLETED", nullable=False)
    external_communication_id: Mapped[str | None] = mapped_column(String(128), nullable=True)


class FollowUp(Base, Timestamped):
    __tablename__ = "crm_followups"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crm_tenants.id"), index=True, nullable=False)
    lead_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("crm_leads.id"), nullable=True, index=True)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("crm_customers.id"), nullable=True, index=True)
    subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    safe_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="PENDING", nullable=False)
    assigned_to: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reminder_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class LeadStageHistory(Base):
    __tablename__ = "crm_lead_stage_history"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crm_tenants.id"), index=True, nullable=False)
    lead_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crm_leads.id"), index=True, nullable=False)
    from_stage: Mapped[str] = mapped_column(String(24), nullable=False)
    to_stage: Mapped[str] = mapped_column(String(24), nullable=False)
    actor: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
