"""Customer lifecycle events, risk records and the immutable CRM timeline."""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base


class CustomerLifecycleEvent(Base):
    """Append-only lifecycle transition record. The transition itself must go
    through the state machine; this row is written by the lifecycle service."""
    __tablename__ = "crm_customer_lifecycle_events"
    __table_args__ = (Index("ix_crm_lc_tenant_customer", "tenant_id", "customer_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crm_tenants.id"), index=True, nullable=False)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crm_customers.id"), index=True, nullable=False)
    from_state: Mapped[str] = mapped_column(String(24), nullable=False)
    to_state: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    trigger: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actor: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    related_external_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    related_external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class CustomerRisk(Base):
    """Explainable, rule-based risk aggregation. Machine-learning prediction
    stays in AIOps; CRM stores the latest explainable result and references."""
    __tablename__ = "crm_customer_risk"
    __table_args__ = (Index("ix_crm_risk_tenant_customer", "tenant_id", "customer_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crm_tenants.id"), index=True, nullable=False)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crm_customers.id"), index=True, nullable=False)
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_event_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    override_level: Mapped[str | None] = mapped_column(String(16), nullable=True)
    override_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    override_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    effective_level: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class TimelineEntry(Base):
    """Immutable, append-only normalized domain event for the customer timeline."""
    __tablename__ = "crm_timeline"
    __table_args__ = (Index("ix_crm_timeline_tenant_customer", "tenant_id", "customer_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crm_tenants.id"), index=True, nullable=False)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("crm_customers.id"), nullable=True, index=True)
    lead_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("crm_leads.id"), nullable=True, index=True)
    category: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    safe_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor: Mapped[str | None] = mapped_column(String(128), nullable=True)
    external_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
