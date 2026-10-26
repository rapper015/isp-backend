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
    op.add_column("oss_orders", sa.Column("idempotency_key", sa.String(255)))
    op.add_column("oss_orders", sa.Column("request_fingerprint", sa.String(64)))
    op.create_unique_constraint("uq_oss_order_create_idempotency", "oss_orders",
                                ["tenant_id", "idempotency_key"])


def downgrade():
    op.drop_constraint("uq_oss_order_create_idempotency", "oss_orders", type_="unique")
    op.drop_column("oss_orders", "request_fingerprint")
    op.drop_column("oss_orders", "idempotency_key")
