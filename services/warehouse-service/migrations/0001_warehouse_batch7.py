"""Warehouse: initial schema (Master Spec Batch 7d)

Revision ID: 0001
Revises:
Create Date: 2026-08-31
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alembic import op  # noqa: E402
from app.database import Base  # noqa: E402
import app.models  # noqa: E402, F401
import app.events  # noqa: E402, F401

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_all(Base.metadata)


def downgrade():
    for t in ("wh_kpi", "wh_revenue_trend", "wh_profitability", "wh_analytics_cluster",
              "wh_ecosystem_metric", "wh_outbox"):
        op.drop_table(t)
