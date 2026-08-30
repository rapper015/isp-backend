"""Add the Observability & Service Assurance Milestone 9 baseline schema.

Creates all `ass_` tables from SQLAlchemy metadata (additive convention used by
the other services). Downgrade drops tables in reverse dependency order.
"""
from alembic import op

revision = "0001_assurance_milestone9"
down_revision = None
branch_labels = None
depends_on = None

_TABLES = (
    "ass_async_tasks", "ass_audit_log", "ass_inbox_messages", "ass_outbox_events",
    "ass_service_customer_impact_rules", "ass_service_topology", "ass_service_owners",
    "ass_service_dependencies", "ass_service_components", "ass_service_definitions",
    "ass_maintenance_exceptions", "ass_maintenance_windows", "ass_slo_window_states",
    "ass_slo_versions", "ass_slo_definitions", "ass_sli_measurements", "ass_sli_definitions",
    "ass_notification_deliveries", "ass_alert_silences", "ass_alert_routes",
    "ass_alert_events", "ass_alerts", "ass_alert_definition_tests", "ass_alert_definitions",
    "ass_change_events", "ass_root_cause_evidence", "ass_root_cause_hypotheses",
    "ass_postmortem_action_items", "ass_postmortems", "ass_incident_ticket_links",
    "ass_incident_actions", "ass_incident_communications", "ass_incident_responders",
    "ass_incident_commanders", "ass_incident_customer_impacts", "ass_incident_service_impacts",
    "ass_incident_alert_links", "ass_incident_events", "ass_incidents",
    "ass_dashboard_definitions", "ass_metric_registry", "ass_network_observations",
    "ass_synthetic_results", "ass_synthetic_checks", "ass_kpi_targets",
    "ass_kpi_measurements", "ass_kpi_definitions",
)


def upgrade() -> None:
    from app.database import Base
    import app.models  # noqa: F401
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    from app.database import Base
    import app.models  # noqa: F401
    for name in _TABLES:
        if name in Base.metadata.tables:
            Base.metadata.tables[name].drop(bind=op.get_bind(), checkfirst=True)
