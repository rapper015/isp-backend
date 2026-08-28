"""Add the Milestone 3 network-control schema.

Creates the M3 tables (policies, bandwidth/QoS/FUP catalog, decisions,
enforcement, control actions, session timeline, router readiness) from the
SQLAlchemy metadata, following the additive migration convention.
"""
from alembic import op

revision = "0006_network_control"
down_revision = "0005_nas_full_orchestration"
branch_labels = None
depends_on = None

_M3_TABLES = (
    "nc_router_readiness",
    "nc_session_timeline",
    "nc_control_actions",
    "nc_policy_drift",
    "nc_device_policy_bindings",
    "nc_enforcement_attempts",
    "nc_enforcement_actions",
    "nc_policy_decisions",
    "nc_policy_overrides",
    "nc_subscriber_policy_assignments",
    "nc_fup_counters",
    "nc_fup_policies",
    "nc_qos_profiles",
    "nc_traffic_classes",
    "nc_bandwidth_profiles",
    "nc_policy_versions",
    "nc_policies",
)


def upgrade() -> None:
    from app.database import Base
    import app.models  # noqa: F401
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    from app.database import Base
    import app.models  # noqa: F401
    for name in _M3_TABLES:
        Base.metadata.tables[name].drop(bind=op.get_bind(), checkfirst=True)
