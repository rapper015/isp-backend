"""Add the Device-Management Milestone 7 baseline schema.

Creates all device-management tables from SQLAlchemy metadata, following the
additive migration convention used by the other services. Downgrade drops
tables in reverse dependency order.
"""
from alembic import op

revision = "0001_device_management_milestone7"
down_revision = None
branch_labels = None
depends_on = None

_TABLES = (
    "device_firmware_exceptions", "device_firmware_verifications",
    "device_firmware_deployments", "device_firmware_rollout_stages",
    "device_firmware_rollouts", "device_firmware_cohorts", "device_firmware_approvals",
    "device_firmware_compatibility", "device_firmware_artifacts",
    "device_diagnostic_results", "device_diagnostic_jobs",
    "device_action_events", "device_actions",
    "device_configuration_drift", "device_configuration_verifications",
    "device_configuration_steps", "device_configuration_jobs",
    "device_configuration_snapshots", "device_observed_states", "device_desired_states",
    "device_profile_assignment_decisions", "device_profile_assignment_rules",
    "device_profile_parameters", "device_configuration_profile_versions",
    "device_configuration_profiles",
    "device_cpe_events", "device_cpe_capability_snapshots", "device_cpe_telemetry",
    "device_cpe_secrets", "device_cpe_ownership_history", "device_cpe_relationships",
    "device_cpe_onboarding", "managed_cpes",
    "acs_instance_credentials", "acs_capabilities", "acs_device_bindings",
    "acs_health", "acs_instances",
    "device_supported_diagnostics", "device_supported_actions", "device_vendor_quirks",
    "device_parameter_mappings", "device_parameter_definitions", "device_capabilities",
    "device_data_models", "device_model_variants", "device_models", "device_manufacturers",
    "device_audit_log", "device_inbox_messages", "device_outbox_events", "device_tenants",
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
