"""KYC cases and documents. Document contents are never stored in plaintext;
references point to private encrypted storage and only masked identifiers are
kept."""
import uuid
from datetime import datetime

from sqlalchemy import (JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Timestamped
from ..database import Base


class KycCase(Base, Timestamped):
    __tablename__ = "crm_kyc_cases"
    __table_args__ = (Index("ix_crm_kyc_tenant_customer", "tenant_id", "customer_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crm_tenants.id"), index=True, nullable=False)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("crm_customers.id"), nullable=True, index=True)
    lead_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("crm_leads.id"), nullable=True, index=True)
    kyc_type: Mapped[str] = mapped_column(String(24), default="INDIVIDUAL", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="NOT_STARTED", nullable=False, index=True)
    verification_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    verification_method: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reviewer_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    risk_flags: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    rejection_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    reverify_by: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class KycDocument(Base, Timestamped):
    __tablename__ = "crm_kyc_documents"
    __table_args__ = (Index("ix_crm_kyc_doc_tenant_case", "tenant_id", "kyc_case_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crm_tenants.id"), index=True, nullable=False)
    kyc_case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crm_kyc_cases.id"), index=True, nullable=False)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("crm_customers.id"), nullable=True, index=True)
    document_type: Mapped[str] = mapped_column(String(32), nullable=False)
    masked_identifier: Mapped[str | None] = mapped_column(String(64), nullable=True)
    storage_reference: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    verification_state: Mapped[str] = mapped_column(String(16), default="PENDING", nullable=False)
    rejection_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    verified_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
