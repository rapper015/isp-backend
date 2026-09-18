"""Add branch contact details and package/IP-pool assignments."""

import sqlalchemy as sa
from alembic import op

revision = "0004_branch_profile"
down_revision = "0003_franchise_profile"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("crm_branches", sa.Column("profile", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))


def downgrade() -> None:
    op.drop_column("crm_branches", "profile")
