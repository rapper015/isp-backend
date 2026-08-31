"""workforce: initial schema (Master Spec Batch 2)

Revision ID: 0001
Revises:
Create Date: 2026-08-30
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alembic import op  # noqa: E402
from app.database import Base  # noqa: E402
import app.models  # noqa: E402, F401

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TABLES = (
    "workforce_technician", "workforce_work_order", "workforce_assignment",
    "workforce_appointment", "workforce_visit", "workforce_proof",
    "workforce_inventory_item", "workforce_consumable", "workforce_consumption",
    "workforce_shift", "workforce_feedback", "workforce_escalation",
    "workforce_field_sla", "workforce_kpi", "workforce_checklist_template",
    "workforce_site_check", "workforce_handover", "workforce_audit_log",
    "workforce_outbox", "workforce_inbox",
)


def upgrade():
    op.create_all(Base.metadata)


def downgrade():
    for t in TABLES:
        op.drop_table(t)
