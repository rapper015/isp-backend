"""add optional administrator department

Revision ID: 0011_user_departments
Revises: 0010_user_access_scopes
"""
from alembic import op
import sqlalchemy as sa

revision = "0011_user_departments"
down_revision = "0010_user_access_scopes"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("platform_users", sa.Column("department_id", sa.Uuid(), sa.ForeignKey("platform_departments.id", ondelete="SET NULL")))
    op.create_index("ix_platform_users_department_id", "platform_users", ["department_id"])

def downgrade():
    op.drop_index("ix_platform_users_department_id", table_name="platform_users")
    op.drop_column("platform_users", "department_id")
