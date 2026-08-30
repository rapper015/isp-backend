"""Add the CRM Master Spec Batch 6 ecosystem/SLA tables.

Additive migration: create_all is idempotent, so existing tables are untouched
and only the new Batch 6 tables are created.
"""
from alembic import op

revision = "0002_crm_ecosystem"
down_revision = "0001_crm_baseline"
branch_labels = None
depends_on = None

BATCH6_TABLES = (
    "crm_partners", "crm_partner_performance", "crm_partner_hierarchy",
    "crm_federation_links", "crm_ticket_sla", "crm_ticket_escalations",
    "crm_ticket_suggestions", "crm_reseller_regulatory",
)


def upgrade() -> None:
    from app.database import Base
    import app.models  # noqa: F401
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    for t in BATCH6_TABLES:
        op.drop_table(t)
