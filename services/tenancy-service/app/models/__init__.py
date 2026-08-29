"""Model registry for the Tenancy Service. Importing this module registers all
tables on Base.metadata and marks tenant-owned models for fail-closed routing."""
from .messaging import AsyncTask, AuditLog, InboxMessage, OutboxEvent  # noqa: F401
from .base import Base, Timestamped, UuidPk  # noqa: F401
from .tenants import (  # noqa: F401
    Entitlement,
    FeatureFlag,
    Quota,
    Tenant,
    TenantConfiguration,
    TenantConfigurationVersion,
    TenantDatabase,
    TenantDomain,
    TenantEntitlement,
    TenantFeature,
    TenantHealth,
    TenantQuota,
    TenantSecret,
)
from .organizations import (  # noqa: F401
    CustomerOwnership,
    CustomerTransfer,
    DataAccessGrant,
    OrganizationUnit,
    OrganizationUnitHistory,
    OwnershipHistory,
    Partner,
    PartnerAgreement,
    PartnerAgreementVersion,
    PartnerBranding,
    PartnerFinancialAccount,
    PartnerMembership,
    PartnerPolicy,
    PartnerRelationship,
    PartnerServiceScope,
    PartnerStatusHistory,
    PartnerTerritory,
)
from .access import (  # noqa: F401
    ApiCredential,
    Approval,
    ImpersonationSession,
    MembershipRole,
    OrganizationMembership,
    Permission,
    Role,
    RolePermission,
    RoleTemplate,
    ServiceAccount,
    SodConstraint,
    TenantMembership,
    UserIdentity,
)
from .financial import (  # noqa: F401
    AccountingPeriod,
    CommissionAdjustment,
    CommissionAgreement,
    CommissionClawback,
    CommissionEarning,
    CommissionPlan,
    CommissionPlanVersion,
    CommissionRule,
    JournalEntry,
    JournalLine,
    LedgerAccount,
    LedgerBalanceProjection,
    PartnerSettlement,
    PartnerStatement,
    RevenueShareRule,
    SettlementCycle,
    SettlementDispute,
    SettlementLine,
    SettlementPayout,
    SettlementReconciliation,
    WalletAccount,
    WalletEntry,
)
from .reporting import AggregateProjection, ExportJob, ReportSnapshot  # noqa: F401

from ..routing import tenant_owned

# Tenant-owned models require a validated TenantContext before access.
_TENANT_OWNED = (
    TenantConfiguration, TenantConfigurationVersion, TenantSecret,
    TenantDomain, TenantFeature, TenantEntitlement, TenantQuota, TenantHealth,
    OrganizationUnit, OrganizationUnitHistory, Partner, PartnerRelationship,
    PartnerAgreement, PartnerAgreementVersion, PartnerTerritory, PartnerServiceScope,
    PartnerMembership, PartnerBranding, PartnerPolicy, PartnerFinancialAccount,
    PartnerStatusHistory, CustomerOwnership, OwnershipHistory, CustomerTransfer,
    DataAccessGrant, MembershipRole, OrganizationMembership, Role, RolePermission,
    SodConstraint, Approval, ServiceAccount, ApiCredential, ImpersonationSession,
    CommissionPlan, CommissionPlanVersion, CommissionRule, CommissionAgreement,
    CommissionEarning, CommissionAdjustment, CommissionClawback, RevenueShareRule,
    SettlementCycle, PartnerSettlement, SettlementLine, SettlementDispute,
    SettlementPayout, SettlementReconciliation, PartnerStatement, WalletAccount,
    WalletEntry, JournalEntry, JournalLine, AccountingPeriod, LedgerBalanceProjection,
    ReportSnapshot, ExportJob,
)
for _model in _TENANT_OWNED:
    tenant_owned(_model)
