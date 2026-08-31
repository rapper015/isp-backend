"""Customer Application Form (CAF) records. CAF approval state is separate from
KYC state and network activation state."""
import uuid
from datetime import date, datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Timestamped
from ..database import Base


class CafRecord(Base, Timestamped):
    __tablename__ = "crm_caf_records"
    __table_args__ = (
        UniqueConstraint("tenant_id", "caf_number", name="uq_crm_caf_tenant_number"),
        Index("ix_crm_caf_tenant_customer", "tenant_id", "customer_id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crm_tenants.id"), index=True, nullable=False)
    caf_number: Mapped[str] = mapped_column(String(128), nullable=False)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("crm_customers.id"), nullable=True, index=True)
    lead_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("crm_leads.id"), nullable=True, index=True)
    lead_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    application_date: Mapped[date | None] = mapped_column(nullable=True)
    franchise_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("crm_franchises.id"), nullable=True, index=True)
    branch_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("crm_branches.id"), nullable=True, index=True)
    requested_services: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    declaration_accepted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    document_checklist: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="DRAFT", nullable=False, index=True)
    reviewer_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    generated_document_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
