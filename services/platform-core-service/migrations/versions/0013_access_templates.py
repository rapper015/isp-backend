"""organization access templates

Revision ID: 0013_access_templates
Revises: 0012_retire_access_control_menu
"""
from alembic import op
import sqlalchemy as sa

revision = "0013_access_templates"
down_revision = "0012_retire_access_control_menu"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("platform_access_templates",
        sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("tenant_id", sa.Uuid()), sa.Column("target_type", sa.String(16), nullable=False),
        sa.Column("name", sa.String(128), nullable=False), sa.Column("code", sa.String(32), nullable=False),
        sa.Column("description", sa.Text()), sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("scope", "tenant_id", "code", name="uq_access_template_scope_code"))
    op.create_index("ix_access_template_scope", "platform_access_templates", ["scope"])
    op.create_index("ix_access_template_tenant", "platform_access_templates", ["tenant_id"])
    op.create_index("ix_access_template_target_type", "platform_access_templates", ["target_type"])
    op.create_table("platform_access_template_permissions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("template_id", sa.Uuid(), sa.ForeignKey("platform_access_templates.id", ondelete="CASCADE"), nullable=False),
        sa.Column("permission_id", sa.Uuid(), sa.ForeignKey("platform_permissions.id", ondelete="CASCADE"), nullable=False),
        sa.UniqueConstraint("template_id", "permission_id", name="uq_access_template_permission"))
    op.create_index("ix_access_template_permission_template", "platform_access_template_permissions", ["template_id"])
    op.create_table("platform_organization_access_templates",
        sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("target_type", sa.String(16), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("template_id", sa.Uuid(), sa.ForeignKey("platform_access_templates.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("assigned_by", sa.Uuid(), sa.ForeignKey("platform_users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("target_type", "target_id", name="uq_organization_access_template_target"))
    op.create_index("ix_org_access_template_target", "platform_organization_access_templates", ["target_type", "target_id"])
    op.create_index("ix_org_access_template_template", "platform_organization_access_templates", ["template_id"])

def downgrade():
    op.drop_table("platform_organization_access_templates")
    op.drop_table("platform_access_template_permissions")
    op.drop_table("platform_access_templates")
