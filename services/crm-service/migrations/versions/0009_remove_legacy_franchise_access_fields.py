"""remove legacy franchise role and account manager profile fields

Revision ID: 0009_remove_franchise_access
Revises: 0008_canonical_indian_phones
"""
from alembic import op


revision = "0009_remove_franchise_access"
down_revision = "0008_canonical_indian_phones"
branch_labels = None
depends_on = None


def upgrade():
    # Franchise access is assigned through organization access templates, while
    # roles belong to login accounts. Remove the obsolete duplicate metadata.
    op.execute("""
        UPDATE crm_franchises
        SET profile = (profile::jsonb
            - 'role'
            - 'role_id'
            - 'account_manager'
            - 'account_manager_id')::json
        WHERE profile IS NOT NULL
          AND (profile::jsonb ?| ARRAY['role', 'role_id', 'account_manager', 'account_manager_id'])
    """)


def downgrade():
    # Removed metadata cannot be reconstructed. The canonical access-template
    # assignment and administrator roles are intentionally left unchanged.
    pass
