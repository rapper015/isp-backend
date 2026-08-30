"""siem: initial schema (Master Spec Batch 1)

Revision ID: 0001
Revises:
Create Date: 2026-08-30
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alembic import op  # noqa: E402
import sqlalchemy as sa  # noqa: E402
from app.database import Base  # noqa: E402
import app.models  # noqa: E402, F401

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TABLES = (
    "sec_security_event", "sec_evidence_block", "sec_policy", "sec_policy_violation",
    "sec_retention_policy", "sec_consent", "sec_data_request", "sec_case",
    "sec_case_event", "sec_audit_log", "sec_vulnerability", "sec_li_request",
    "sec_outbox", "sec_inbox",
)


def upgrade():
    op.create_all(Base.metadata)


def downgrade():
    for t in TABLES:
        op.drop_table(t)
