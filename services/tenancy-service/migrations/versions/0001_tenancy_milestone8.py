"""Add the Tenancy / Franchise Milestone 8 baseline schema.

Creates all tenancy tables from SQLAlchemy metadata (additive convention used
by the other services). Downgrade drops tables in reverse dependency order.
"""
from alembic import op

revision = "0001_tenancy_milestone8"
down_revision = None
branch_labels = None
depends_on = None

_TABLES = (
    "ten_async_tasks", "ten_audit_log", "ten_inbox_messages", "ten_outbox_events",
    "ten_tenant_health", "ten_tenant_quotas", "ten_quotas", "ten_tenant_entitlements",
    "ten_entitlements", "ten_tenant_features", "ten_feature_flags", "ten_tenant_secrets",
    "ten_tenant_config_versions", "ten_tenant_configs", "ten_tenant_domains",
    "ten_tenant_databases", "ten_tenants",
    "ten_export_jobs", "ten_aggregate_projections", "ten_report_snapshots",
    "ten_ledger_balance_projections", "ten_accounting_periods", "ten_journal_lines",
    "ten_journal_entries", "ten_ledger_accounts", "ten_wallet_entries", "ten_wallet_accounts",
    "ten_partner_statements", "ten_settlement_reconciliations", "ten_settlement_payouts",
    "ten_settlement_disputes", "ten_settlement_lines", "ten_partner_settlements",
    "ten_settlement_cycles", "ten_revenue_share_rules", "ten_commission_clawbacks",
    "ten_commission_adjustments", "ten_commission_earnings", "ten_commission_agreements",
    "ten_commission_rules", "ten_commission_plan_versions", "ten_commission_plans",
    "ten_impersonation_sessions", "ten_api_credentials", "ten_service_accounts",
    "ten_approvals", "ten_sod_constraints", "ten_role_permissions", "ten_roles",
    "ten_role_templates", "ten_permissions", "ten_org_memberships", "ten_membership_roles",
    "ten_memberships", "ten_user_identities",
    "ten_data_access_grants", "ten_customer_transfers", "ten_ownership_history",
    "ten_customer_ownerships", "ten_partner_status_history", "ten_partner_financial_accounts",
    "ten_partner_policies", "ten_partner_branding", "ten_partner_memberships",
    "ten_partner_service_scopes", "ten_partner_territories", "ten_partner_agreement_versions",
    "ten_partner_agreements", "ten_partner_relationships", "ten_partners",
    "ten_org_unit_history", "ten_organization_units",
)


def upgrade() -> None:
    from app.database import Base
    import app.models  # noqa: F401
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    from app.database import Base
    import app.models  # noqa: F401
    for name in _TABLES:
        if name in Base.metadata.tables:
            Base.metadata.tables[name].drop(bind=op.get_bind(), checkfirst=True)
