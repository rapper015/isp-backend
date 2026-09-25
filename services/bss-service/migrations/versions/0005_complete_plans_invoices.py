"""Complete plan and invoice management fields.

Revision ID: 0005_complete_plans_invoices
Revises: 0004_invoice_imports
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_complete_plans_invoices"
down_revision = "0004_invoice_imports"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    plan_columns = {item["name"] for item in inspector.get_columns("plans")}
    for column in (
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("billing_cycle_days", sa.Integer(), nullable=False, server_default="30"),
    ):
        if column.name not in plan_columns:
            op.add_column("plans", column)
    if "ix_plans_tenant_id" not in {item["name"] for item in inspector.get_indexes("plans")}:
        op.create_index("ix_plans_tenant_id", "plans", ["tenant_id"])

    invoice_columns = {item["name"] for item in inspector.get_columns("invoices")}
    for column in (
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column("billing_period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("billing_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("subtotal", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("tax_amount", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("line_items", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, server_default=sa.func.now()),
    ):
        if column.name not in invoice_columns:
            op.add_column("invoices", column)
    if "ix_invoices_tenant_id" not in {item["name"] for item in inspector.get_indexes("invoices")}:
        op.create_index("ix_invoices_tenant_id", "invoices", ["tenant_id"])
    op.execute("UPDATE invoices SET subtotal = amount WHERE subtotal = 0")


def downgrade():
    # This migration is additive and may be stamped over a schema originally
    # created by metadata.create_all. Destructive downgrade is intentionally
    # disabled to avoid removing columns owned by that baseline.
    pass
