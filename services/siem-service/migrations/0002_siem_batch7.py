"""Add the SIEM Master Spec Batch 7 compliance-ops tables.

Additive migration: create_all is idempotent, so existing tables are untouched
and only the new Batch 7 tables are created.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alembic import op  # noqa: E402
from app.database import Base  # noqa: E402
import app.models  # noqa: E402, F401

revision = "0002_siem_batch7"
down_revision = "0001_siem_batch1"
branch_labels = None
depends_on = None

BATCH7_TABLES = ("sec_circle_region", "sec_geo_block_rule", "sec_threat_playbook",
                 "sec_adaptive_mfa_rule")


def upgrade():
    Base.metadata.create_all(bind=op.get_bind())


def downgrade():
    for t in BATCH7_TABLES:
        op.drop_table(t)
