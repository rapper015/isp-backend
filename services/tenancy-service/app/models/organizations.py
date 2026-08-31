"""Organization hierarchy, partners (franchise/reseller/distributor/...),
agreements, territories, customer/service ownership, transfers and grants."""
import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, Timestamped, UuidPk


class OrganizationUnit(Base, Timestamped, UuidPk):
    __tablename__ = "ten_organization_units"
    __table_args__ = (Index("ix_ten_org_unit_tenant", "tenant_id"), Index("ix_ten_org_path", "path"))

    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    unit_type: Mapped[str] = mapped_column(String(24), default="BRANCH", nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    path: Mapped[str] = mapped_column(String(512), nullable=False)  # materialized path
    status: Mapped[str] = mapped_column(String(24), default="ACTIVE", nullable=False)
    unit_metadata: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class OrganizationUnitHistory(Base, Timestamped, UuidPk):
    __tablename__ = "ten_org_unit_history"

    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    org_unit_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    change_type: Mapped[str] = mapped_column(String(40), nullable=False)  # CREATED|REPARENTED|RENAMED|...
    before: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    after: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    changed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Partner(Base, Timestamped, UuidPk):
    __tablename__ = "ten_partners"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_ten_partner_code"),
        Index("ix_ten_partner_status", "status"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    partner_type: Mapped[str] = mapped_column(String(32), default="FRANCHISE", nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    legal_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="PROSPECT", nullable=False)
    contact_person: Mapped[str | None] = mapped_column(String(160), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    email: Mapped[str | None] = mapped_column(String(160), nullable=True)
    address: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    tax_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)  # GSTIN / PAN reference
    tax_config_ref: Mapped[str | None] = mapped_column(String(80), nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="INR", nullable=False)
    account_manager: Mapped[str | None] = mapped_column(String(128), nullable=True)
    primary_pop_ref: Mapped[str | None] = mapped_column(String(80), nullable=True)
    redundant_pop_ref: Mapped[str | None] = mapped_column(String(80), nullable=True)
    custom_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    logo_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    org_unit_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    capabilities: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class PartnerRelationship(Base, Timestamped, UuidPk):
    __tablename__ = "ten_partner_relationships"
    __table_args__ = (UniqueConstraint("parent_id", "child_id", "relationship_type", name="uq_ten_partner_rel"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    parent_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    child_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    relationship_type: Mapped[str] = mapped_column(String(32), default="FRANCHISE_OF", nullable=False)
    path: Mapped[str] = mapped_column(String(512), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class PartnerAgreement(Base, Timestamped, UuidPk):
    __tablename__ = "ten_partner_agreements"
    __table_args__ = (Index("ix_ten_partner_agreement", "tenant_id", "partner_id"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    partner_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="DRAFT", nullable=False)
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    customer_ownership_model: Mapped[str] = mapped_column(String(40), default="TENANT_OWNED", nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PartnerAgreementVersion(Base, Timestamped, UuidPk):
    __tablename__ = "ten_partner_agreement_versions"
    __table_args__ = (UniqueConstraint("agreement_id", "version", name="uq_ten_agreement_version"),)

    agreement_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    terms: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    changed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class PartnerTerritory(Base, Timestamped, UuidPk):
    __tablename__ = "ten_partner_territories"
    __table_args__ = (UniqueConstraint("tenant_id", "partner_id", "territory_key", name="uq_ten_partner_territory"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    partner_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    territory_key: Mapped[str] = mapped_column(String(120), nullable=False)
    region: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class PartnerServiceScope(Base, Timestamped, UuidPk):
    __tablename__ = "ten_partner_service_scopes"
    __table_args__ = (UniqueConstraint("tenant_id", "partner_id", "service", name="uq_ten_partner_service_scope"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    partner_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    service: Mapped[str] = mapped_column(String(64), nullable=False)  # CUSTOMER|SUPPORT|WORKFORCE|INVENTORY|...
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    detail: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class PartnerMembership(Base, Timestamped, UuidPk):
    __tablename__ = "ten_partner_memberships"
    __table_args__ = (UniqueConstraint("tenant_id", "partner_id", "user_id", name="uq_ten_partner_membership"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    partner_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="ACTIVE", nullable=False)
    granted_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class PartnerBranding(Base, Timestamped, UuidPk):
    __tablename__ = "ten_partner_branding"
    __table_args__ = (UniqueConstraint("tenant_id", "partner_id", name="uq_ten_partner_branding"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    partner_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    logo_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    theme: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    custom_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)


class PartnerPolicy(Base, Timestamped, UuidPk):
    __tablename__ = "ten_partner_policies"
    __table_args__ = (UniqueConstraint("tenant_id", "partner_id", "policy_key", name="uq_ten_partner_policy"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    partner_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    policy_key: Mapped[str] = mapped_column(String(80), nullable=False)
    value: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class PartnerFinancialAccount(Base, Timestamped, UuidPk):
    __tablename__ = "ten_partner_financial_accounts"
    __table_args__ = (UniqueConstraint("tenant_id", "partner_id", "currency", name="uq_ten_partner_finance_account"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    partner_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    currency: Mapped[str] = mapped_column(String(8), default="INR", nullable=False)
    bank_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)  # encrypted reference
    tax_withholding_pct: Mapped[float | None] = mapped_column(nullable=True)
    tax_config_ref: Mapped[str | None] = mapped_column(String(80), nullable=True)


class PartnerStatusHistory(Base, Timestamped, UuidPk):
    __tablename__ = "ten_partner_status_history"

    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    partner_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    from_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    to_status: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    changed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class CustomerOwnership(Base, Timestamped, UuidPk):
    __tablename__ = "ten_customer_ownerships"
    __table_args__ = (UniqueConstraint("tenant_id", "customer_id", name="uq_ten_customer_ownership"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    customer_id: Mapped[str] = mapped_column(String(80), nullable=False)
    owning_org_unit_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    acquisition_partner_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    servicing_partner_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    billing_owner_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    support_owner_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    network_owner_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    collection_owner_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    owned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OwnershipHistory(Base, Timestamped, UuidPk):
    __tablename__ = "ten_ownership_history"

    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    customer_id: Mapped[str] = mapped_column(String(80), nullable=False)
    change_type: Mapped[str] = mapped_column(String(40), nullable=False)  # ACQUIRED|TRANSFERRED|SCOPE_CHANGED
    before: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    after: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    changed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class CustomerTransfer(Base, Timestamped, UuidPk):
    __tablename__ = "ten_customer_transfers"

    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    customer_id: Mapped[str] = mapped_column(String(80), nullable=False)
    from_owner_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    to_owner_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    transfer_type: Mapped[str] = mapped_column(String(40), default="PARTNER_TO_TENANT", nullable=False)
    state: Mapped[str] = mapped_column(String(24), default="REQUESTED", nullable=False)
    validation: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class DataAccessGrant(Base, Timestamped, UuidPk):
    __tablename__ = "ten_data_access_grants"

    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    granting_org_unit_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    receiving_org_unit_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_scope: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    permission: Mapped[str] = mapped_column(String(80), nullable=False)
    purpose: Mapped[str | None] = mapped_column(Text, nullable=True)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
