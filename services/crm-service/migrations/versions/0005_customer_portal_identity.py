"""Separate customer portal credentials from network credentials."""
import sqlalchemy as sa
from alembic import op

revision = "0005_customer_portal"
down_revision = "0004_branch_profile"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "crm_customer_portal_identities",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("crm_tenants.id"), nullable=False, index=True),
        sa.Column("customer_id", sa.Uuid(), sa.ForeignKey("crm_customers.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("username", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="ACTIVE", index=True),
        sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("failed_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "customer_id", name="uq_crm_portal_customer"),
        sa.UniqueConstraint("tenant_id", "username", name="uq_crm_portal_username"),
    )


def downgrade():
    op.drop_table("crm_customer_portal_identities")
