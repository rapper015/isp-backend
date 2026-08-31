"""Add the AI & Intelligence Layer Milestone 10 baseline schema.

Creates all `ai_` tables from SQLAlchemy metadata (additive convention used by
the other services). Downgrade drops tables in reverse dependency order.
"""
from alembic import op

revision = "0001_intelligence_milestone10"
down_revision = None
branch_labels = None
depends_on = None

_TABLES = (
    "ai_async_tasks", "ai_audit_log", "ai_inbox_messages", "ai_outbox_events",
    "ai_remediation_outcomes", "ai_remediation_steps", "ai_remediation_approvals",
    "ai_remediation_intents", "ai_kill_switches", "ai_remediation_policies",
    "ai_recommendations", "ai_capacity_forecasts", "ai_failure_predictions",
    "ai_retention_candidates", "ai_churn_scores", "ai_fraud_action_recommendations",
    "ai_fraud_decisions", "ai_fraud_evidence", "ai_fraud_cases", "ai_fraud_signals",
    "ai_fraud_rules",
    "ai_model_monitoring", "ai_model_deployments", "ai_model_registry", "ai_model_cards",
    "ai_training_runs",
    "ai_online_feature_values", "ai_feature_values", "ai_feature_definitions",
    "ai_consent_records", "ai_lineage_links", "ai_pipeline_runs", "ai_data_quality_checks",
    "ai_dataset_snapshots", "ai_analytical_records", "ai_raw_events", "ai_data_contracts",
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
