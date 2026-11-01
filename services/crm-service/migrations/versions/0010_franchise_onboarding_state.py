"""Persist resumable franchise onboarding state.

Revision ID: 0010_franchise_onboarding
Revises: 0009_remove_franchise_access
"""
from alembic import op
import sqlalchemy as sa

revision = "0010_franchise_onboarding"
down_revision = "0009_remove_franchise_access"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {item["name"] for item in inspector.get_columns("crm_franchises")}
    for name, column_type in (
        ("onboarding_state", sa.String(32)),
        ("onboarding_fingerprint", sa.String(64)),
        ("onboarding_admin_id", sa.String(64)),
        ("onboarding_error", sa.String(500)),
        ("onboarding_requested_status", sa.String(16)),
    ):
        if name not in columns:
            op.add_column("crm_franchises", sa.Column(name, column_type))
    indexes = {item["name"] for item in inspector.get_indexes("crm_franchises")}
    if "ix_crm_franchises_onboarding_state" not in indexes:
        op.create_index("ix_crm_franchises_onboarding_state", "crm_franchises", ["onboarding_state"])


def downgrade():
    inspector = sa.inspect(op.get_bind())
    indexes = {item["name"] for item in inspector.get_indexes("crm_franchises")}
    if "ix_crm_franchises_onboarding_state" in indexes:
        op.drop_index("ix_crm_franchises_onboarding_state", table_name="crm_franchises")
    columns = {item["name"] for item in inspector.get_columns("crm_franchises")}
    for name in (
        "onboarding_requested_status", "onboarding_error", "onboarding_admin_id",
        "onboarding_fingerprint", "onboarding_state",
    ):
        if name in columns:
            op.drop_column("crm_franchises", name)
