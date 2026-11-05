"""Create the initial NMS monitoring and operations schema.

Revision ID: 0001
Revises:
"""
from alembic import op


revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


_NMS_TABLES = (
    "nas_devices",
    "health_observations",
    "nms_escalation_policy",
    "nms_config_snapshot",
    "nms_approval_sla",
    "nms_cache_strategy",
    "nms_degradation_rule",
    "nms_queue_saturation",
    "nms_runbook",
    "nms_anomaly_heatmap",
)


def upgrade() -> None:
    from app.database import Base
    import app.models  # noqa: F401

    # checkfirst=True (the SQLAlchemy default) adopts databases previously
    # initialized by the legacy FastAPI create_all startup hook. Alembic then
    # records this revision without deleting or recreating their data.
    Base.metadata.create_all(
        bind=op.get_bind(),
        tables=[Base.metadata.tables[name] for name in _NMS_TABLES],
        checkfirst=True,
    )


def downgrade() -> None:
    from app.database import Base
    import app.models  # noqa: F401

    for name in reversed(_NMS_TABLES):
        Base.metadata.tables[name].drop(bind=op.get_bind(), checkfirst=True)
