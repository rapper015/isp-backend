"""Add the Workforce Milestone 6 baseline schema.

Creates all workforce-owned tables from SQLAlchemy metadata, following the
additive migration convention used by the AAA, CRM, BSS, OSS and support
services. Downgrade drops tables in reverse dependency order.
"""
from alembic import op

revision = "0001_workforce_milestone6"
down_revision = None
branch_labels = None
depends_on = None

# Dependency order: children before parents so FKs are dropped cleanly.
_TABLES = (
    "workforce_work_order_results", "workforce_work_order_number_sequences",
    "workforce_offline_commands", "workforce_field_escalations",
    "workforce_field_sla_pauses", "workforce_field_sla_instances",
    "workforce_quality_reviews", "workforce_customer_acknowledgements",
    "workforce_field_attachments", "workforce_proof_of_work",
    "workforce_checklist_responses", "workforce_work_order_checklists",
    "workforce_device_installations", "workforce_material_usage",
    "workforce_material_requirements", "workforce_work_order_blockers",
    "workforce_time_entries", "workforce_dispatch_plans",
    "workforce_visit_checkouts", "workforce_visit_checkins",
    "workforce_field_visits", "workforce_appointments",
    "workforce_work_order_assignments", "workforce_work_order_relationships",
    "workforce_work_order_events", "workforce_work_orders",
    "workforce_technician_status_log", "workforce_technician_shifts",
    "workforce_technician_availability", "workforce_technician_certifications",
    "workforce_technician_skills", "workforce_technicians",
    "workforce_holidays", "workforce_calendars", "workforce_field_sla_targets",
    "workforce_field_sla_policy_versions", "workforce_field_sla_policies",
    "workforce_service_areas", "workforce_checklist_items",
    "workforce_checklist_template_versions", "workforce_checklist_templates",
    "workforce_work_order_template_versions", "workforce_work_order_templates",
    "workforce_work_order_types",
    "workforce_audit_log", "workforce_inbox_messages", "workforce_outbox_events",
    "workforce_tenants",
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
