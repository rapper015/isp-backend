"""Make remote user creation safely retryable.

Revision ID: 0015_user_idempotency
Revises: 0014_organization_owners
"""
from alembic import op
import sqlalchemy as sa

revision = "0015_user_idempotency"
down_revision = "0014_organization_owners"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("platform_users", sa.Column("idempotency_key", sa.String(200), nullable=True))
    op.add_column("platform_users", sa.Column("request_fingerprint", sa.String(64), nullable=True))
    op.create_index("ix_platform_users_idempotency_key", "platform_users", ["idempotency_key"], unique=True)


def downgrade():
    op.drop_index("ix_platform_users_idempotency_key", table_name="platform_users")
    op.drop_column("platform_users", "request_fingerprint")
    op.drop_column("platform_users", "idempotency_key")
