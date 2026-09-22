"""Add the complete reseller/franchise profile document."""

import sqlalchemy as sa
from alembic import op

revision = "0003_franchise_profile"
down_revision = "0002_crm_ecosystem"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("crm_franchises")}
    if "profile" not in columns:
        op.add_column("crm_franchises", sa.Column("profile", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("crm_franchises")}
    if "profile" in columns:
        op.drop_column("crm_franchises", "profile")
