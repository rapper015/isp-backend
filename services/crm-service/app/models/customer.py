"""Customer identity, profile, contacts, addresses, ownership and external
references. CRM owns these; BSS/OSS/AAA/IPAM data is only referenced."""
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (JSON, Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, func)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Timestamped
from ..database import Base


class Franchise(Base, Timestamped):
    """Customer/lead ownership reference. Financial franchise operations stay in BSS."""
    __tablename__ = "crm_franchises"
    __table_args__ = (UniqueConstraint("tenant_id", "franchise_code", name="uq_crm_franchise_tenant_code"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crm_tenants.id"), index=True, nullable=False)
    franchise_code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", nullable=False)


class Branch(Base, Timestamped):
    __tablename__ = "crm_branches"
    __table_args__ = (UniqueConstraint("tenant_id", "branch_code", name="uq_crm_branch_tenant_code"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crm_tenants.id"), index=True, nullable=False)
    franchise_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("crm_franchises.id"), nullable=True, index=True)
    branch_code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", nullable=False)


class Customer(Base, Timestamped):
    """Authoritative customer identity and profile.

    The Milestone 0 minimal mapping (customer_code, full_name, phone, email,
    status) is preserved for backward compatibility while the richer fields
    are added.
    """
    __tablename__ = "crm_customers"
    __table_args__ = (
        UniqueConstraint("tenant_id", "customer_number", name="uq_crm_customer_tenant_number"),
        UniqueConstraint("tenant_id", "customer_code", name="uq_crm_customer_tenant_code"),
        Index("ix_crm_customer_tenant_phone", "tenant_id", "phone"),
        Index("ix_crm_customer_tenant_email", "tenant_id", "email"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crm_tenants.id"), index=True, nullable=False)
    customer_number: Mapped[str] = mapped_column(String(64), nullable=False)
    customer_code: Mapped[str] = mapped_column(String(64), nullable=False)  # Milestone 0 identifier
    caf_number: Mapped[str | None] = mapped_column(String(128), nullable=True)
    customer_type: Mapped[str] = mapped_column(String(24), default="INDIVIDUAL", nullable=False)
    # --- identity ---
    legal_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)  # Milestone 0 field
    first_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    middle_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    company_trading_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    father_or_guardian_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    date_of_birth: Mapped[date | None] = mapped_column(nullable=True)
    incorporation_date: Mapped[date | None] = mapped_column(nullable=True)
    gstin: Mapped[str | None] = mapped_column(String(32), nullable=True)
    pan_reference: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tax_category_reference: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # --- profile ---
    phone: Mapped[str] = mapped_column(String(32), nullable=False)  # Milestone 0 field
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)  # Milestone 0 field
    primary_language: Mapped[str | None] = mapped_column(String(32), nullable=True)
    preferred_channel: Mapped[str | None] = mapped_column(String(32), nullable=True)
    acquisition_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # --- ownership ---
    franchise_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("crm_franchises.id"), nullable=True, index=True)
    branch_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("crm_branches.id"), nullable=True, index=True)
    area: Mapped[str | None] = mapped_column(String(128), nullable=True)
    account_manager_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # --- state (CRM-owned only) ---
    lifecycle_state: Mapped[str] = mapped_column(String(24), default="PROSPECT", nullable=False, index=True)
    risk_level: Mapped[str] = mapped_column(String(16), default="UNKNOWN", nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), default="onboarding", nullable=False, index=True)  # Milestone 0 field (legacy status)
    activation_date: Mapped[date | None] = mapped_column(nullable=True)
    closure_date: Mapped[date | None] = mapped_column(nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class Contact(Base, Timestamped):
    """Normalized customer contact / authorized person."""
    __tablename__ = "crm_contacts"
    __table_args__ = (Index("ix_crm_contact_tenant_customer", "tenant_id", "customer_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crm_tenants.id"), index=True, nullable=False)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crm_customers.id"), index=True, nullable=False)
    role: Mapped[str] = mapped_column(String(32), default="CONTACT_PERSON", nullable=False)
    contact_person_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mobile: Mapped[str | None] = mapped_column(String(32), nullable=True)
    alternate_mobile: Mapped[str | None] = mapped_column(String(32), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    alternate_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    landline: Mapped[str | None] = mapped_column(String(32), nullable=True)
    whatsapp: Mapped[str | None] = mapped_column(String(32), nullable=True)
    verification_state: Mapped[str] = mapped_column(String(16), default="UNVERIFIED", nullable=False)
    otp_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    communication_preference: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    consent_state: Mapped[str] = mapped_column(String(24), default="NOT_PROVIDED", nullable=False)
    valid_from: Mapped[date | None] = mapped_column(nullable=True)
    valid_to: Mapped[date | None] = mapped_column(nullable=True)
    unsubscribed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)


class Address(Base, Timestamped):
    """Structured, versioned customer address."""
    __tablename__ = "crm_addresses"
    __table_args__ = (Index("ix_crm_address_tenant_customer", "tenant_id", "customer_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crm_tenants.id"), index=True, nullable=False)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crm_customers.id"), index=True, nullable=False)
    address_type: Mapped[str] = mapped_column(String(24), nullable=False)
    country: Mapped[str | None] = mapped_column(String(64), nullable=True)
    state: Mapped[str | None] = mapped_column(String(128), nullable=True)
    district: Mapped[str | None] = mapped_column(String(128), nullable=True)
    city: Mapped[str | None] = mapped_column(String(128), nullable=True)
    zipcode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    door_number: Mapped[str | None] = mapped_column(String(128), nullable=True)
    street: Mapped[str | None] = mapped_column(String(255), nullable=True)
    area: Mapped[str | None] = mapped_column(String(255), nullable=True)
    colony: Mapped[str | None] = mapped_column(String(255), nullable=True)
    building: Mapped[str | None] = mapped_column(String(255), nullable=True)
    landmark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    house_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    formatted_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    latitude: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=True)
    longitude: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=True)
    geolocation_accuracy: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=True)
    verification_state: Mapped[str] = mapped_column(String(16), default="UNVERIFIED", nullable=False)
    valid_from: Mapped[date | None] = mapped_column(nullable=True)
    valid_to: Mapped[date | None] = mapped_column(nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    superseded_by_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)


class ServiceLocation(Base, Timestamped):
    """A customer-owned service location referenced by OSS/Workforce."""
    __tablename__ = "crm_service_locations"
    __table_args__ = (UniqueConstraint("tenant_id", "service_location_number", name="uq_crm_service_location_number"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crm_tenants.id"), index=True, nullable=False)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crm_customers.id"), index=True, nullable=False)
    address_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("crm_addresses.id"), nullable=True)
    service_location_number: Mapped[str] = mapped_column(String(64), nullable=False)
    alias: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="PLANNED", nullable=False)


class CustomerOwnership(Base, Timestamped):
    """Customer ownership by franchise/branch/account manager/salesperson."""
    __tablename__ = "crm_customer_ownership"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crm_tenants.id"), index=True, nullable=False)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crm_customers.id"), index=True, nullable=False)
    owner_type: Mapped[str] = mapped_column(String(24), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    valid_from: Mapped[date | None] = mapped_column(nullable=True)
    valid_to: Mapped[date | None] = mapped_column(nullable=True)


class ExternalReference(Base, Timestamped):
    """Read-only references to downstream service aggregates (BSS/OSS/AAA/NMS/IPAM)."""
    __tablename__ = "crm_external_references"
    __table_args__ = (Index("ix_crm_extref_tenant_customer", "tenant_id", "customer_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crm_tenants.id"), index=True, nullable=False)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("crm_customers.id"), nullable=True, index=True)
    lead_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("crm_leads.id"), nullable=True, index=True)
    service_name: Mapped[str] = mapped_column(String(32), nullable=False)
    external_type: Mapped[str] = mapped_column(String(64), nullable=False)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    external_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    safe_projection: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
