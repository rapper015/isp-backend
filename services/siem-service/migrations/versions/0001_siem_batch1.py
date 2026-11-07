"""Create the initial SIEM security and compliance schema.

Revision ID: 0001
Revises:
"""
from alembic import op


revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


TABLES = (
    "sec_security_event",
    "sec_evidence_block",
    "sec_policy",
    "sec_policy_violation",
    "sec_retention_policy",
    "sec_consent",
    "sec_data_request",
    "sec_case",
    "sec_case_event",
    "sec_audit_log",
    "sec_vulnerability",
    "sec_li_request",
    "sec_outbox",
    "sec_inbox",
)


def upgrade() -> None:
    from app.database import Base
    import app.models  # noqa: F401

    Base.metadata.create_all(
        bind=op.get_bind(),
        tables=[Base.metadata.tables[name] for name in TABLES],
        checkfirst=True,
    )


def downgrade() -> None:
    from app.database import Base
    import app.models  # noqa: F401

    for name in reversed(TABLES):
        Base.metadata.tables[name].drop(bind=op.get_bind(), checkfirst=True)
