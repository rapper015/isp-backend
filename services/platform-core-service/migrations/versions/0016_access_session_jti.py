"""Bind access tokens to revocable login sessions.

Revision ID: 0016_access_session_jti
Revises: 0015_user_idempotency
"""
from alembic import op
import sqlalchemy as sa


revision = "0016_access_session_jti"
down_revision = "0015_user_idempotency"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {item["name"] for item in inspector.get_columns("platform_refresh_tokens")}
    if "access_jti" not in columns:
        op.add_column(
            "platform_refresh_tokens",
            sa.Column("access_jti", sa.String(length=36), nullable=True),
        )
    indexes = {item["name"] for item in inspector.get_indexes("platform_refresh_tokens")}
    if "ix_platform_refresh_tokens_access_jti" not in indexes:
        op.create_index(
            "ix_platform_refresh_tokens_access_jti",
            "platform_refresh_tokens",
            ["access_jti"],
            unique=True,
        )


def downgrade():
    op.drop_index(
        "ix_platform_refresh_tokens_access_jti",
        table_name="platform_refresh_tokens",
    )
    op.drop_column("platform_refresh_tokens", "access_jti")
