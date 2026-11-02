"""Scope RADIUS infrastructure nodes to tenants."""
from alembic import op
import sqlalchemy as sa

revision = "0007_radius_server_tenant"
down_revision = "0006_network_control"
branch_labels = None
depends_on = None

def upgrade():
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    columns = {column["name"] for column in inspector.get_columns("radius_servers")}
    if "tenant_id" not in columns:
        op.add_column("radius_servers", sa.Column("tenant_id", sa.Uuid(), nullable=True))

    inspector = sa.inspect(connection)
    tenant_foreign_key = any(
        foreign_key.get("constrained_columns") == ["tenant_id"]
        and foreign_key.get("referred_table") == "aaa_tenants"
        for foreign_key in inspector.get_foreign_keys("radius_servers")
    )
    if not tenant_foreign_key:
        op.create_foreign_key(
            "fk_radius_servers_tenant",
            "radius_servers",
            "aaa_tenants",
            ["tenant_id"],
            ["id"],
        )

    tenant_index = any(
        index.get("column_names") == ["tenant_id"]
        for index in inspector.get_indexes("radius_servers")
    )
    if not tenant_index:
        op.create_index("ix_radius_servers_tenant_id", "radius_servers", ["tenant_id"])

def downgrade():
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    for index in inspector.get_indexes("radius_servers"):
        if index.get("column_names") == ["tenant_id"]:
            op.drop_index(index["name"], table_name="radius_servers")
            break
    for foreign_key in inspector.get_foreign_keys("radius_servers"):
        if (
            foreign_key.get("constrained_columns") == ["tenant_id"]
            and foreign_key.get("referred_table") == "aaa_tenants"
        ):
            op.drop_constraint(foreign_key["name"], "radius_servers", type_="foreignkey")
            break
    columns = {column["name"] for column in sa.inspect(connection).get_columns("radius_servers")}
    if "tenant_id" in columns:
        op.drop_column("radius_servers", "tenant_id")
