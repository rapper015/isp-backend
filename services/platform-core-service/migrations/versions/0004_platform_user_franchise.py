"""add franchise scope to platform users

Revision ID: 0004_platform_user_franchise
Revises: 0003_platform_user_mobile
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_platform_user_franchise"
down_revision = "0003_platform_user_mobile"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("platform_users", sa.Column("franchise_id", sa.Uuid(), nullable=True))
    op.create_index("ix_platform_users_franchise_id", "platform_users", ["franchise_id"])


def downgrade():
    op.drop_index("ix_platform_users_franchise_id", table_name="platform_users")
    op.drop_column("platform_users", "franchise_id")
