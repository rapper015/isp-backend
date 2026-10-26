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
    op.add_column("crm_franchises", sa.Column("onboarding_state", sa.String(32)))
    op.add_column("crm_franchises", sa.Column("onboarding_fingerprint", sa.String(64)))
    op.add_column("crm_franchises", sa.Column("onboarding_admin_id", sa.String(64)))
    op.add_column("crm_franchises", sa.Column("onboarding_error", sa.String(500)))
    op.add_column("crm_franchises", sa.Column("onboarding_requested_status", sa.String(16)))
    op.create_index("ix_crm_franchises_onboarding_state", "crm_franchises", ["onboarding_state"])


def downgrade():
    op.drop_index("ix_crm_franchises_onboarding_state", table_name="crm_franchises")
    op.drop_column("crm_franchises", "onboarding_requested_status")
    op.drop_column("crm_franchises", "onboarding_error")
    op.drop_column("crm_franchises", "onboarding_admin_id")
    op.drop_column("crm_franchises", "onboarding_fingerprint")
    op.drop_column("crm_franchises", "onboarding_state")
