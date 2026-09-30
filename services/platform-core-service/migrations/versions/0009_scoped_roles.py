"""add department-scoped custom roles

Revision ID: 0009_scoped_roles
Revises: 0008_department_codes
"""
from alembic import op
import sqlalchemy as sa

revision = "0009_scoped_roles"
down_revision = "0008_department_codes"
branch_labels = None
depends_on = None

def upgrade():
    op.alter_column("platform_roles", "name", type_=sa.String(128), existing_type=sa.String(64))
    op.add_column("platform_roles", sa.Column("display_name", sa.String(128)))
    op.add_column("platform_roles", sa.Column("code", sa.String(32)))
    op.add_column("platform_roles", sa.Column("description", sa.Text()))
    op.add_column("platform_roles", sa.Column("scope", sa.String(16), nullable=False, server_default="SYSTEM"))
    op.add_column("platform_roles", sa.Column("tenant_id", sa.Uuid()))
    op.add_column("platform_roles", sa.Column("department_id", sa.Uuid(), sa.ForeignKey("platform_departments.id", ondelete="RESTRICT")))
    op.add_column("platform_roles", sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.create_index("ix_platform_roles_code", "platform_roles", ["code"])
    op.create_index("ix_platform_roles_scope", "platform_roles", ["scope"])
    op.create_index("ix_platform_roles_tenant_id", "platform_roles", ["tenant_id"])
    op.create_index("ix_platform_roles_department_id", "platform_roles", ["department_id"])
    op.create_unique_constraint("uq_platform_role_scope_code", "platform_roles", ["scope", "tenant_id", "code"])

def downgrade():
    op.drop_constraint("uq_platform_role_scope_code", "platform_roles", type_="unique")
    for name in ["ix_platform_roles_department_id", "ix_platform_roles_tenant_id", "ix_platform_roles_scope", "ix_platform_roles_code"]: op.drop_index(name, table_name="platform_roles")
    for name in ["enabled", "department_id", "tenant_id", "scope", "description", "code", "display_name"]: op.drop_column("platform_roles", name)
    op.alter_column("platform_roles", "name", type_=sa.String(64), existing_type=sa.String(128))
