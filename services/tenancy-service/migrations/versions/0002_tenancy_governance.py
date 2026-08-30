"""Add the Tenancy Master Spec Batch 4 governance tables.

Additive migration: create_all is idempotent, so existing tables are untouched
and only the new Batch 4 governance tables are created.
"""
from alembic import op

revision = "0002_tenancy_governance"
down_revision = "0001_tenancy_milestone8"
branch_labels = None
depends_on = None

BATCH4_TABLES = (
    "ten_notification", "ten_campaign", "ten_campaign_recipient", "ten_campaign_metric",
    "ten_usage_meter", "ten_cost_record", "ten_governance_policy", "ten_compliance_check",
    "ten_threat_hunt", "ten_service_chain", "ten_insight", "ten_knowledge_doc",
    "ten_procurement_order", "ten_inventory_forecast", "ten_roi_record", "ten_scaling_rule",
    "ten_mesh_link", "ten_cloud_abstraction", "ten_translation",
)


def upgrade() -> None:
    from app.database import Base
    import app.models  # noqa: F401
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    for t in BATCH4_TABLES:
        op.drop_table(t)
