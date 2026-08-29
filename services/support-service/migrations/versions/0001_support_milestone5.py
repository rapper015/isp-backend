"""Add the Support Milestone 5 baseline schema.

Creates all support-owned tables from SQLAlchemy metadata, following the
additive migration convention used by the AAA, CRM, BSS and OSS services.
"""
from alembic import op

revision = "0001_support_milestone5"
down_revision = None
branch_labels = None
depends_on = None

_TABLES = (
    "support_knowledge_usage", "support_knowledge_articles", "support_csat",
    "support_ticket_resolutions", "support_actions", "support_diagnostic_snapshots",
    "support_ticket_escalations", "support_ticket_sla_pauses", "support_ticket_slas",
    "support_ticket_tags", "support_ticket_relationships", "support_ticket_watchers",
    "support_ticket_attachments", "support_ticket_comments", "support_ticket_events",
    "support_tickets", "support_ticket_number_sequences",
    "support_sla_targets", "support_sla_policy_versions", "support_sla_policies",
    "support_holidays", "support_calendars", "support_routing_rules",
    "support_agent_memberships", "support_teams", "support_queues",
    "support_ticket_subcategories", "support_ticket_categories", "support_ticket_types",
    "support_audit_log", "support_inbox_messages", "support_outbox_events", "support_tenants",
)


def upgrade() -> None:
    from app.database import Base
    import app.models  # noqa: F401
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    from app.database import Base
    import app.models  # noqa: F401
    for name in _TABLES:
        Base.metadata.tables[name].drop(bind=op.get_bind(), checkfirst=True)
