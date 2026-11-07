"""Add the SIEM compliance operations tables.

Revision ID: 0002_siem_batch7
Revises: 0001
"""
from alembic import op


revision = "0002_siem_batch7"
down_revision = "0001"
branch_labels = None
depends_on = None


BATCH7_TABLES = (
    "sec_circle_region",
    "sec_geo_block_rule",
    "sec_threat_playbook",
    "sec_adaptive_mfa_rule",
)


def upgrade() -> None:
    from app.database import Base
    import app.models  # noqa: F401

    Base.metadata.create_all(
        bind=op.get_bind(),
        tables=[Base.metadata.tables[name] for name in BATCH7_TABLES],
        checkfirst=True,
    )


def downgrade() -> None:
    from app.database import Base
    import app.models  # noqa: F401

    for name in reversed(BATCH7_TABLES):
        Base.metadata.tables[name].drop(bind=op.get_bind(), checkfirst=True)
