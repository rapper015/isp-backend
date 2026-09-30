"""Expand service plans into the tenant package catalog.

Revision ID: 0008_package_catalog
Revises: 0007_consolidated
"""
from alembic import op
import sqlalchemy as sa

revision = "0008_package_catalog"
down_revision = "0007_consolidated"
branch_labels = None
depends_on = None


def upgrade():
    for column in (
        sa.Column("package_type", sa.String(32), nullable=False, server_default="BASE_PLAN"),
        sa.Column("package_data_type", sa.String(32), nullable=False, server_default="UNLIMITED"),
        sa.Column("apply_to", sa.String(32), nullable=False, server_default="BOTH"),
        sa.Column("policy_mode", sa.String(24), nullable=False, server_default="KBPS"),
        sa.Column("available_to_all_franchises", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("self_care_portal_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("network_config", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("fup_config", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("billing_config", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("service_config", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("promotion_content", sa.Text(), nullable=True),
        sa.Column("comment", sa.String(200), nullable=True),
    ):
        op.add_column("plans", column)

    op.create_table("plan_prices",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("plan_id", sa.Uuid(), sa.ForeignKey("plans.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("price", sa.Numeric(12, 2), nullable=False),
        sa.Column("duration", sa.Integer(), nullable=False),
        sa.Column("duration_unit", sa.String(16), nullable=False, server_default="DAY"),
        sa.Column("status", sa.String(16), nullable=False, server_default="ACTIVE"),
        sa.UniqueConstraint("plan_id", "name", name="uq_plan_price_name"))
    op.create_table("plan_franchise_availability",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False, index=True),
        sa.Column("plan_id", sa.Uuid(), sa.ForeignKey("plans.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("franchise_id", sa.Uuid(), nullable=False, index=True),
        sa.UniqueConstraint("plan_id", "franchise_id", name="uq_plan_franchise"))
    op.create_table("plan_ott_mappings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("plan_id", sa.Uuid(), sa.ForeignKey("plans.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("provider_id", sa.String(128), nullable=False),
        sa.Column("provider_name", sa.String(128), nullable=False),
        sa.Column("provider_plan_id", sa.String(128), nullable=False),
        sa.Column("provider_plan_name", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="ACTIVE"))


def downgrade():
    op.drop_table("plan_ott_mappings")
    op.drop_table("plan_franchise_availability")
    op.drop_table("plan_prices")
    for name in ("comment", "promotion_content", "service_config", "billing_config", "fup_config", "network_config", "self_care_portal_enabled", "available_to_all_franchises", "policy_mode", "apply_to", "package_data_type", "package_type"):
        op.drop_column("plans", name)
