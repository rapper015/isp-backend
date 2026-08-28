"""Add the OSS Milestone 2 baseline schema.

Creates all OSS-owned tables from SQLAlchemy metadata, following the additive
migration convention used by the AAA and CRM services.
"""
from alembic import op

revision = "0001_oss_milestone2"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    from app.database import Base
    import app.models  # noqa: F401
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    from app.database import Base
    import app.models  # noqa: F401
    for name in (
        "oss_manual_interventions", "oss_workflow_events", "oss_saga_step_attempts",
        "oss_saga_steps", "oss_saga_instances", "oss_resource_reservations",
        "oss_resource_inventory", "oss_order_commands", "oss_order_status_history",
        "oss_order_events", "oss_orders", "oss_service_subscriptions",
        "oss_inbox_messages", "oss_outbox_events", "oss_tenants",
    ):
        Base.metadata.tables[name].drop(bind=op.get_bind(), checkfirst=True)
