"""Add durable customer onboarding workflow records.

Revision ID: 0011_customer_onboarding
Revises: 0010_franchise_onboarding
"""
from alembic import op
import sqlalchemy as sa


revision = "0011_customer_onboarding"
down_revision = "0010_franchise_onboarding"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "crm_customer_onboarding_operations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("customer_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("failed_step", sa.String(32)),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(500)),
        sa.Column("local_resources", sa.JSON(), nullable=False),
        sa.Column("order_result", sa.JSON(), nullable=False),
        sa.Column("activation_result", sa.JSON(), nullable=False),
        sa.Column("final_result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["crm_tenants.id"]),
        sa.ForeignKeyConstraint(["customer_id"], ["crm_customers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_crm_customer_onboarding_key"),
        sa.UniqueConstraint("customer_id", name="uq_crm_customer_onboarding_customer"),
    )
    op.create_index("ix_crm_customer_onboarding_tenant_id", "crm_customer_onboarding_operations", ["tenant_id"])
    op.create_index("ix_crm_customer_onboarding_customer_id", "crm_customer_onboarding_operations", ["customer_id"])
    op.create_index(
        "ix_crm_customer_onboarding_tenant_state",
        "crm_customer_onboarding_operations",
        ["tenant_id", "state"],
    )


def downgrade():
    op.drop_index("ix_crm_customer_onboarding_tenant_state", table_name="crm_customer_onboarding_operations")
    op.drop_index("ix_crm_customer_onboarding_customer_id", table_name="crm_customer_onboarding_operations")
    op.drop_index("ix_crm_customer_onboarding_tenant_id", table_name="crm_customer_onboarding_operations")
    op.drop_table("crm_customer_onboarding_operations")
