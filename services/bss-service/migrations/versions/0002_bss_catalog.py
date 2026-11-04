"""Add the BSS Master Spec Batch 5 catalog/monetization tables.

Additive migration: create_all is idempotent, so existing tables are untouched
and only the new Batch 5 tables are created.
"""
from alembic import op

revision = "0002_bss_catalog"
down_revision = "0001_bss_milestone4"
branch_labels = None
depends_on = None

BATCH5_TABLES = (
    "bss_product_bundles", "bss_service_catalog", "bss_enterprise_catalog",
    "bss_vendors", "bss_sla_pricing_tier", "bss_api_marketplace", "bss_budget_plans",
    "bss_cost_centers", "bss_profit_centers", "bss_feature_adoption",
    "bss_partner_sla_metric", "bss_churn_records", "bss_trial_records",
    "bss_product_stickiness", "bss_commission_records", "bss_wallet_ledger",
    "bss_coupon", "bss_redemption", "bss_service_composition",
    "bss_expense_record", "bss_margin_optimization", "bss_referral",
)


def upgrade() -> None:
    from app.database import Base
    import app.revenue.models  # noqa: F401
    Base.metadata.create_all(
        bind=op.get_bind(),
        tables=[Base.metadata.tables[name] for name in BATCH5_TABLES],
    )


def downgrade() -> None:
    from app.database import Base
    import app.revenue.models  # noqa: F401
    for name in reversed(BATCH5_TABLES):
        Base.metadata.tables[name].drop(bind=op.get_bind(), checkfirst=True)
