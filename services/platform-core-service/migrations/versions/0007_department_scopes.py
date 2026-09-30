"""separate platform and tenant department scopes

Revision ID: 0007_department_scopes
Revises: 0006_departments
"""
from alembic import op
import sqlalchemy as sa

revision = "0007_department_scopes"
down_revision = "0006_departments"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("platform_departments", sa.Column("scope", sa.String(16), nullable=False, server_default="TENANT"))
    op.create_index("ix_platform_departments_scope", "platform_departments", ["scope"])
    op.alter_column("platform_departments", "tenant_id", nullable=True)
    op.drop_constraint("uq_platform_department_tenant_code", "platform_departments", type_="unique")
    op.create_unique_constraint("uq_platform_department_scope_code", "platform_departments", ["scope", "tenant_id", "code"])
    op.create_check_constraint("ck_platform_department_owner", "platform_departments", "(scope = 'PLATFORM' AND tenant_id IS NULL) OR (scope = 'TENANT' AND tenant_id IS NOT NULL)")

def downgrade():
    op.drop_constraint("ck_platform_department_owner", "platform_departments", type_="check")
    op.drop_constraint("uq_platform_department_scope_code", "platform_departments", type_="unique")
    op.create_unique_constraint("uq_platform_department_tenant_code", "platform_departments", ["tenant_id", "code"])
    op.alter_column("platform_departments", "tenant_id", nullable=False)
    op.drop_index("ix_platform_departments_scope", table_name="platform_departments")
    op.drop_column("platform_departments", "scope")
