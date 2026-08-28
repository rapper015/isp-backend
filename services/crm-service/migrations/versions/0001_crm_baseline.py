"""Add the CRM Milestone 1 baseline schema.

Creates all CRM-owned tables from SQLAlchemy metadata, following the additive
migration convention used by the AAA service.
"""
from alembic import op

revision = "0001_crm_baseline"
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
        "crm_audit_log", "crm_timeline", "crm_customer_risk", "crm_customer_lifecycle_events",
        "crm_kyc_documents", "crm_kyc_cases", "crm_caf_records", "crm_customer_aliases",
        "crm_customer_ownership", "crm_external_references", "crm_service_locations",
        "crm_addresses", "crm_contacts", "crm_customers", "crm_lead_stage_history",
        "crm_followups", "crm_lead_interactions", "crm_lead_assignments", "crm_leads",
        "crm_branches", "crm_franchises", "crm_tenants", "crm_outbox", "crm_consumer_inbox",
    ):
        Base.metadata.tables[name].drop(bind=op.get_bind(), checkfirst=True)
