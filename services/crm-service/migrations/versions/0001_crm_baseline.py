"""Add the CRM Milestone 1 baseline schema.

Only tables owned by this revision are created.  Passing the complete live
metadata here would also create tables introduced by later revisions and make
a fresh installation fail when those revisions run.
"""
from alembic import op

revision = "0001_crm_baseline"
down_revision = None
branch_labels = None
depends_on = None

BASELINE_TABLES = (
    "crm_audit_log", "crm_timeline", "crm_customer_risk", "crm_customer_lifecycle_events",
    "crm_kyc_documents", "crm_kyc_cases", "crm_caf_records", "crm_customer_aliases",
    "crm_customer_ownership", "crm_external_references", "crm_service_locations",
    "crm_addresses", "crm_contacts", "crm_customers", "crm_lead_stage_history",
    "crm_followups", "crm_lead_interactions", "crm_lead_assignments", "crm_leads",
    "crm_branches", "crm_franchises", "crm_tenants", "crm_outbox", "crm_consumer_inbox",
)


def upgrade() -> None:
    from app.database import Base
    import app.models  # noqa: F401
    tables = [Base.metadata.tables[name] for name in BASELINE_TABLES if name in Base.metadata.tables]
    Base.metadata.create_all(bind=op.get_bind(), tables=tables)


def downgrade() -> None:
    from app.database import Base
    import app.models  # noqa: F401
    for name in BASELINE_TABLES:
        table = Base.metadata.tables.get(name)
        if table is not None:
            table.drop(bind=op.get_bind(), checkfirst=True)
