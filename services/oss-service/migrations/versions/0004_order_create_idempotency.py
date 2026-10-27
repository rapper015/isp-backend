"""Honor create-order idempotency keys.

Revision ID: 0004_order_create_idempotency
Revises: 0003_subscriber_imports
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_order_create_idempotency"
down_revision = "0003_subscriber_imports"
branch_labels = None
depends_on = None


def upgrade():
    # Earlier baseline migrations intentionally call create_all() with the
    # current model metadata.  On a fresh database that can create these
    # fields before this revision is reached; on an older database they are
    # absent.  Keep this revision safe in both cases.
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("oss_orders")}
    if "idempotency_key" not in columns:
        op.add_column("oss_orders", sa.Column("idempotency_key", sa.String(255)))
    if "request_fingerprint" not in columns:
        op.add_column("oss_orders", sa.Column("request_fingerprint", sa.String(64)))
    constraints = {item["name"] for item in inspector.get_unique_constraints("oss_orders")}
    if "uq_oss_order_create_idempotency" not in constraints:
        op.create_unique_constraint("uq_oss_order_create_idempotency", "oss_orders",
                                    ["tenant_id", "idempotency_key"])


def downgrade():
    op.drop_constraint("uq_oss_order_create_idempotency", "oss_orders", type_="unique")
    op.drop_column("oss_orders", "request_fingerprint")
    op.drop_column("oss_orders", "idempotency_key")
