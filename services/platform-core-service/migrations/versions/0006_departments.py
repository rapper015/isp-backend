"""add tenant-scoped departments

Revision ID: 0006_departments
Revises: 0005_unique_user_contacts
"""
from alembic import op
import sqlalchemy as sa

revision = "0006_departments"
down_revision = "0005_unique_user_contacts"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        "platform_departments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tenant_id", "code", name="uq_platform_department_tenant_code"),
    )
    op.create_index("ix_platform_departments_tenant_id", "platform_departments", ["tenant_id"])

def downgrade():
    op.drop_index("ix_platform_departments_tenant_id", table_name="platform_departments")
    op.drop_table("platform_departments")
