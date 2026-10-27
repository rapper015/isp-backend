"""Create IPAM storage and add tenant ownership to existing pools."""
from alembic import op
import sqlalchemy as sa

revision = "0001_tenant_scoped_ipam"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    tables = set(inspector.get_table_names())
    if "ip_pools" not in tables:
        op.create_table(
            "ip_pools",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("tenant_id", sa.Uuid(), nullable=False),
            sa.Column("pool_code", sa.String(64), nullable=False, unique=True),
            sa.Column("network_cidr", sa.String(64), nullable=False),
        )
    else:
        columns = {column["name"] for column in inspector.get_columns("ip_pools")}
        if "tenant_id" not in columns:
            op.add_column("ip_pools", sa.Column("tenant_id", sa.Uuid(), nullable=True))
    indexes = {index["name"] for index in sa.inspect(connection).get_indexes("ip_pools")}
    if "ix_ip_pools_tenant_id" not in indexes:
        op.create_index("ix_ip_pools_tenant_id", "ip_pools", ["tenant_id"])
    if "ip_addresses" not in tables:
        op.create_table(
            "ip_addresses",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("pool_id", sa.Uuid(), sa.ForeignKey("ip_pools.id"), nullable=False),
            sa.Column("address", sa.String(64), nullable=False, unique=True),
            sa.Column("status", sa.String(16), nullable=False, server_default="available"),
            sa.Column("subscriber_id", sa.Uuid(), nullable=True),
        )
        op.create_index("ix_ip_addresses_address", "ip_addresses", ["address"])
        op.create_index("ix_ip_addresses_subscriber_id", "ip_addresses", ["subscriber_id"])


def downgrade() -> None:
    # Tenant ownership is a security boundary and must not be silently removed.
    pass
