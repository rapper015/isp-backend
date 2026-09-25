"""Add recurring billing schedules and run history.

Revision ID: 0006_recurring_billing
Revises: 0005_complete_plans_invoices
"""
from alembic import op

revision = "0006_recurring_billing"
down_revision = "0005_complete_plans_invoices"
branch_labels = None
depends_on = None


def upgrade():
    from app.database import Base
    import app.models  # noqa: F401
    Base.metadata.tables["billing_schedules"].create(bind=op.get_bind(), checkfirst=True)
    Base.metadata.tables["billing_runs"].create(bind=op.get_bind(), checkfirst=True)


def downgrade():
    op.drop_table("billing_runs")
    op.drop_table("billing_schedules")
