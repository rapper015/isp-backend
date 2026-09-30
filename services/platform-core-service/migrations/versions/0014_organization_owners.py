"""Add explicit organization ownership to operator accounts.

Revision ID: 0014_organization_owners
Revises: 0013_access_templates
"""
from alembic import op
import sqlalchemy as sa

revision = "0014_organization_owners"
down_revision = "0013_access_templates"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("platform_users", sa.Column("is_organization_owner", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_index("ix_platform_users_is_organization_owner", "platform_users", ["is_organization_owner"])
    # Existing onboarding created one TENANT_ADMIN/FRANCHISE_ADMIN account per
    # organization. Mark the oldest such account as its initial owner.
    op.execute("""
        UPDATE platform_users u SET is_organization_owner = TRUE
        WHERE u.id IN (
          SELECT DISTINCT ON (COALESCE(u2.franchise_id, u2.tenant_id)) u2.id
          FROM platform_users u2
          JOIN platform_user_roles ur ON ur.user_id = u2.id
          JOIN platform_roles r ON r.id = ur.role_id
          WHERE COALESCE(u2.franchise_id, u2.tenant_id) IS NOT NULL
            AND r.name IN ('TENANT_ADMIN', 'FRANCHISE_ADMIN')
          ORDER BY COALESCE(u2.franchise_id, u2.tenant_id), u2.created_at, u2.id
        )
    """)
    op.alter_column("platform_users", "is_organization_owner", server_default=None)


def downgrade():
    op.drop_index("ix_platform_users_is_organization_owner", table_name="platform_users")
    op.drop_column("platform_users", "is_organization_owner")
