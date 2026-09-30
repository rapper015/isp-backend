"""add scoped administrator access

Revision ID: 0010_user_access_scopes
Revises: 0009_scoped_roles
"""
from alembic import op
import sqlalchemy as sa

revision = "0010_user_access_scopes"
down_revision = "0009_scoped_roles"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        "platform_user_access_scopes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("platform_users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scope_type", sa.String(16), nullable=False),
        sa.Column("scope_id", sa.Uuid()),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("platform_users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "scope_type", "scope_id", name="uq_platform_user_access_scope"),
        sa.CheckConstraint("scope_type IN ('PLATFORM','TENANT','FRANCHISE','BRANCH')", name="ck_platform_user_access_scope_type"),
        sa.CheckConstraint("(scope_type = 'PLATFORM' AND scope_id IS NULL) OR (scope_type <> 'PLATFORM' AND scope_id IS NOT NULL)", name="ck_platform_user_access_scope_target"),
    )
    for column in ["user_id", "scope_type", "scope_id", "created_by"]:
        op.create_index(f"ix_platform_user_access_scopes_{column}", "platform_user_access_scopes", [column])
    op.execute("""
        INSERT INTO platform_user_access_scopes (id, user_id, scope_type, scope_id, created_at, updated_at)
        SELECT gen_random_uuid(), id,
          CASE WHEN franchise_id IS NOT NULL THEN 'FRANCHISE' WHEN tenant_id IS NOT NULL THEN 'TENANT' ELSE 'PLATFORM' END,
          COALESCE(franchise_id, tenant_id), CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        FROM platform_users
    """)

def downgrade():
    op.drop_table("platform_user_access_scopes")
