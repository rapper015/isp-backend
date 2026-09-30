"""remove the retired access-control menu permission

Revision ID: 0012_retire_access_control_menu
Revises: 0011_user_departments
"""
from alembic import op


revision = "0012_retire_access_control_menu"
down_revision = "0011_user_departments"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        DELETE FROM platform_role_permissions
        WHERE permission_id IN (
            SELECT id FROM platform_permissions
            WHERE name = 'menu.access_control.view'
        )
        """
    )
    op.execute(
        "DELETE FROM platform_permissions WHERE name = 'menu.access_control.view'"
    )


def downgrade():
    # The retired page no longer exists, so restoring assignments would create
    # unusable access. A future feature may introduce a new permission explicitly.
    pass
