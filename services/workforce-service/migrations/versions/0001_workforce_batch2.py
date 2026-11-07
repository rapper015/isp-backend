"""Create the initial Workforce field-operations schema.

Revision ID: 0001
Revises:
"""
from alembic import op


revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


TABLES = (
    "workforce_technician",
    "workforce_work_order",
    "workforce_assignment",
    "workforce_appointment",
    "workforce_visit",
    "workforce_proof",
    "workforce_inventory_item",
    "workforce_consumable",
    "workforce_consumption",
    "workforce_shift",
    "workforce_feedback",
    "workforce_escalation",
    "workforce_field_sla",
    "workforce_kpi",
    "workforce_checklist_template",
    "workforce_site_check",
    "workforce_handover",
    "workforce_audit_log",
    "workforce_outbox",
    "workforce_inbox",
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
