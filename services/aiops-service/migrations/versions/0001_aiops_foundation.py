"""Establish the AIOps migration baseline.

Revision ID: 0001_aiops_foundation
Revises:
"""

revision = "0001_aiops_foundation"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # AIOps is currently a stateless foundation service. This baseline gives
    # it a versioned PostgreSQL schema before domain tables are introduced.
    pass


def downgrade() -> None:
    pass
