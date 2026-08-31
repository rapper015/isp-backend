"""Add the Intelligence Master Spec Batch 7b operations tables.

Additive migration: create_all is idempotent, so existing tables are untouched
and only the new Batch 7b tables are created.
"""
from alembic import op

revision = "0002_intelligence_ops"
down_revision = "0001_intelligence_milestone10"
branch_labels = None
depends_on = None

BATCH7_TABLES = (
    "ai_personalization_profile", "ai_bottleneck", "ai_automation_coverage",
    "ai_node_profit", "ai_region_profitability",
)


def upgrade() -> None:
    from app.database import Base
    import app.models  # noqa: F401
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    for t in BATCH7_TABLES:
        op.drop_table(t)
