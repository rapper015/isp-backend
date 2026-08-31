"""Request/response schemas (strict — unknown fields are rejected)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# Tenants -------------------------------------------------------------
class TenantCreate(StrictModel):
    name: str
    code: str
    currency: str = "INR"
    country: str | None = None
    legal_name: str | None = None
    isolation_mode: str = "SHARED_SCHEMA_WITH_RLS"


class TenantStatusIn(StrictModel):
    reason: str
    scope: str = "ADMIN_CONSOLE"


class ConfigIn(StrictModel):
    category: str
    config: dict


class DomainIn(StrictModel):
    domain: str
    is_primary: bool = False


class DomainVerifyIn(StrictModel):
    token: str


class FeatureIn(StrictModel):
    code: str
    enabled: bool


class EntitlementIn(StrictModel):
    code: str
    quantity: float | None = None


class QuotaIn(StrictModel):
    kind: str
    limit: float | None = None


class SecretIn(StrictModel):
    name: str
    value: str
    category: str = "INTEGRATION"


# Organizations / partners -------------------------------------------
class OrgUnitCreate(StrictModel):
    unit_type: str = "BRANCH"
    code: str
    name: str
    parent_id: str | None = None


class OrgUnitReparent(StrictModel):
    new_parent_id: str | None = None


class PartnerCreate(StrictModel):
    partner_type: str = "FRANCHISE"
    code: str
    name: str
    org_unit_id: str | None = None
    contact_person: str | None = None
    email: str | None = None
    phone: str | None = None
    currency: str = "INR"


class PartnerStatusIn(StrictModel):
    to_status: str
    reason: str | None = None


class PartnerLinkIn(StrictModel):
    child_partner_id: str
    relationship_type: str = "FRANCHISE_OF"


class AgreementIn(StrictModel):
    code: str
    customer_ownership_model: str = "TENANT_OWNED"


class AgreementVersionIn(StrictModel):
    terms: dict


class ServiceScopeIn(StrictModel):
    service: str
    enabled: bool = True
    detail: dict | None = None


class TerritoryIn(StrictModel):
    territory_key: str
    region: str | None = None
    is_primary: bool = False


class PartnerMembershipIn(StrictModel):
    user_id: str
    role: str


class OwnershipIn(StrictModel):
    customer_id: str
    owning_org_unit_id: str | None = None
    acquisition_partner_id: str | None = None
    servicing_partner_id: str | None = None
    billing_owner_id: str | None = None
    support_owner_id: str | None = None
    network_owner_id: str | None = None
    collection_owner_id: str | None = None


class TransferIn(StrictModel):
    customer_id: str
    to_owner_id: str | None = None
    transfer_type: str = "PARTNER_TO_TENANT"
    reason: str


class TransferApproveIn(StrictModel):
    approved_by: str


class GrantIn(StrictModel):
    granting_org_unit_id: str
    receiving_org_unit_id: str
    resource_type: str
    resource_scope: dict
    permission: str
    purpose: str | None = None
    ends_at: str | None = None
    approved_by: str


# Access --------------------------------------------------------------
class MembershipIn(StrictModel):
    user_id: str


class RoleIn(StrictModel):
    code: str
    name: str


class RolePermissionsIn(StrictModel):
    permission_codes: list[str]


class RoleAssignIn(StrictModel):
    membership_id: str
    role_id: str
    org_unit_id: str | None = None
    scope_kind: str = "TENANT"
    assigned_by: str = "system"


class ApprovalIn(StrictModel):
    operation: str
    reason: str | None = None
    detail: dict | None = None
    resource_type: str | None = None
    resource_id: str | None = None


class ApprovalDecisionIn(StrictModel):
    decision: str
    decided_by: str
    reason: str | None = None


class ServiceAccountIn(StrictModel):
    service: str
    name: str
    permission_codes: list[str]
    ip_restrictions: list[str] | None = None


class CredentialIn(StrictModel):
    service_account_id: str
    name: str
    expires_in_days: int = 90


class ImpersonationIn(StrictModel):
    target_user: str
    reason: str
    ticket_ref: str | None = None
    read_only: bool = True
    ttl_minutes: int = 30


# Commissions ---------------------------------------------------------
class CommissionPlanIn(StrictModel):
    code: str
    name: str


class CommissionRuleIn(StrictModel):
    code: str
    name: str
    basis: str
    calculation_type: str
    rate: float | None = None
    fixed_amount: float | None = None
    currency: str = "INR"
    tiers: list | None = None
    slabs: list | None = None
    exclusions: list | None = None
    threshold: float | None = None
    multiplier: float = 1.0


class CommissionAgreementIn(StrictModel):
    partner_id: str
    plan_id: str


class EarningIn(StrictModel):
    partner_id: str
    source_event_id: str
    source_event_type: str
    basis: str
    basis_amount: float
    customer_id: str | None = None
    service_id: str | None = None
    invoice_ref: str | None = None
    payment_ref: str | None = None
    currency: str = "INR"


class ClawbackIn(StrictModel):
    amount: float | None = None
    kind: str
    source_event_id: str
    reason: str | None = None


class AdjustmentIn(StrictModel):
    amount: float
    kind: str
    reason: str | None = None


# Settlements ---------------------------------------------------------
class CycleIn(StrictModel):
    code: str
    period_start: str
    period_end: str
    currency: str = "INR"


class SettlementIn(StrictModel):
    partner_id: str
    cycle_id: str
    currency: str = "INR"


class PayoutIn(StrictModel):
    amount: float
    method: str = "BANK_TRANSFER"
    reference: str | None = None
    recorded_by: str = "system"


class ReconcileIn(StrictModel):
    detail: dict | None = None
    reconciled_by: str = "system"


class DisputeIn(StrictModel):
    line_id: str | None = None
    reason: str
    submitted_by: str
    evidence: list | None = None


class DisputeResolveIn(StrictModel):
    resolution: str
    adjustment_ref: str | None = None
    response: str | None = None
    resolved_by: str = "system"


class WalletIn(StrictModel):
    entry_type: str
    amount: float
    reference: str | None = None
    reason: str | None = None
    actor: str = "system"


# Reports -------------------------------------------------------------
class ReportIn(StrictModel):
    report_type: str = "overview"
    scope_kind: str = "TENANT"
    scope_id: str | None = None
    period_start: str | None = None
    period_end: str | None = None


class AggregateIn(StrictModel):
    metric: str
    period_key: str | None = None
    dimension: str = "tenant"


class ExportIn(StrictModel):
    export_type: str
    scope_kind: str = "TENANT"
    scope_id: str | None = None
