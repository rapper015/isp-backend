"""Scope RADIUS infrastructure nodes to tenants."""
from alembic import op
import sqlalchemy as sa

revision = "0007_radius_server_tenant"
down_revision = "0006_network_control"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("radius_servers", sa.Column("tenant_id", sa.Uuid(), nullable=True))
    op.create_foreign_key("fk_radius_servers_tenant", "radius_servers", "aaa_tenants", ["tenant_id"], ["id"])
    op.create_index("ix_radius_servers_tenant_id", "radius_servers", ["tenant_id"])

def downgrade():
    op.drop_index("ix_radius_servers_tenant_id", table_name="radius_servers")
    op.drop_constraint("fk_radius_servers_tenant", "radius_servers", type_="foreignkey")
    op.drop_column("radius_servers", "tenant_id")
