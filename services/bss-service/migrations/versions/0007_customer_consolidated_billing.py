"""Customer-owned consolidated billing accounts.

Revision ID: 0007_consolidated
Revises: 0006_recurring_billing
"""
from alembic import op
import sqlalchemy as sa

revision = "0007_consolidated"
down_revision = "0006_recurring_billing"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "billing_accounts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False, index=True),
        sa.Column("customer_id", sa.Uuid(), nullable=False, index=True),
        sa.Column("account_number", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="INR"),
        sa.Column("cycle_days", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("due_days", sa.Integer(), nullable=False, server_default="7"),
        sa.Column("tax_percent", sa.Numeric(6, 3), nullable=False, server_default="0"),
        sa.Column("next_invoice_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("last_invoice_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="active", index=True),
        sa.Column("auto_invoice", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "customer_id", name="uq_billing_account_customer"),
    )
    op.create_table(
        "billing_account_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("billing_account_id", sa.Uuid(), sa.ForeignKey("billing_accounts.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False, index=True),
        sa.Column("customer_id", sa.Uuid(), nullable=False, index=True),
        sa.Column("subscriber_id", sa.Uuid(), nullable=False, index=True),
        sa.Column("plan_id", sa.Uuid(), sa.ForeignKey("plans.id"), nullable=False, index=True),
        sa.Column("description", sa.String(255), nullable=False, server_default="Internet service"),
        sa.Column("custom_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="active", index=True),
        sa.Column("effective_from", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("effective_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("billing_account_id", "subscriber_id", name="uq_billing_item_subscriber"),
    )
    op.add_column("invoices", sa.Column("billing_account_id", sa.Uuid(), nullable=True))
    op.create_index("ix_invoices_billing_account_id", "invoices", ["billing_account_id"])

    # Preserve existing schedules while collapsing multiple connections onto one
    # customer-owned account. DISTINCT ON deterministically chooses the earliest
    # schedule as the account configuration; every subscriber becomes an item.
    op.execute("""
        INSERT INTO billing_accounts
            (id, tenant_id, customer_id, account_number, currency, cycle_days, due_days,
             tax_percent, next_invoice_at, last_invoice_at, status, auto_invoice, created_at, updated_at)
        SELECT DISTINCT ON (tenant_id, customer_id)
            id, tenant_id, customer_id, account_code, currency, cycle_days, due_days,
            tax_percent, next_invoice_at, last_invoice_at, status, auto_invoice, created_at, updated_at
        FROM billing_schedules
        ORDER BY tenant_id, customer_id, created_at, id
        ON CONFLICT (tenant_id, customer_id) DO NOTHING
    """)
    op.execute("""
        INSERT INTO billing_account_items
            (id, billing_account_id, tenant_id, customer_id, subscriber_id, plan_id,
             description, custom_amount, status, effective_from, created_at)
        SELECT gen_random_uuid(), a.id, s.tenant_id, s.customer_id, s.subscriber_id,
               s.plan_id, 'Internet service', s.custom_amount, s.status, s.created_at, s.created_at
        FROM billing_schedules s
        JOIN billing_accounts a ON a.tenant_id = s.tenant_id AND a.customer_id = s.customer_id
        WHERE s.subscriber_id IS NOT NULL
        ON CONFLICT (billing_account_id, subscriber_id) DO NOTHING
    """)


def downgrade():
    op.drop_index("ix_invoices_billing_account_id", table_name="invoices")
    op.drop_column("invoices", "billing_account_id")
    op.drop_table("billing_account_items")
    op.drop_table("billing_accounts")
