"""identity: initial schema (application users)

Revision ID: 0001
Revises:
Create Date: 2026-08-31
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alembic import op  # noqa: E402
from app.database import Base  # noqa: E402
import app.models  # noqa: E402, F401

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_all(Base.metadata)


def downgrade():
    op.drop_table("idp_users")
