"""Add SIEM legal-notice and forensic-automation storage.

Revision ID: 0003_siem_automation
Revises: 0002_siem_batch7
"""
from alembic import op


revision = "0003_siem_automation"
down_revision = "0002_siem_batch7"
branch_labels = None
depends_on = None


AUTOMATION_TABLES = (
    "sec_legal_notice",
    "sec_forensic_investigation",
)


def upgrade() -> None:
    from app.database import Base
    import app.models  # noqa: F401

    Base.metadata.create_all(
        bind=op.get_bind(),
        tables=[Base.metadata.tables[name] for name in AUTOMATION_TABLES],
        checkfirst=True,
    )


def downgrade() -> None:
    from app.database import Base
    import app.models  # noqa: F401

    for name in reversed(AUTOMATION_TABLES):
        Base.metadata.tables[name].drop(bind=op.get_bind(), checkfirst=True)
