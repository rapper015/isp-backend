"""Add the OSS Master Spec Batch 3 tables (assets, config, vendor, enterprise,
infra, security, telemetry).

Additive migration: create_all is idempotent, so existing tables are untouched
and only the new Batch 3 tables are created.
"""
from alembic import op

revision = "0002_oss_batch3"
down_revision = "0001_oss_milestone2"
branch_labels = None
depends_on = None

BATCH3_TABLES = (
    "oss_vendor",
    "oss_network_asset",
    "oss_firmware_log",
    "oss_splitter_node",
    "oss_config_snapshot",
    "oss_config_push",
    "oss_enterprise_sla",
    "oss_vpn_service",
    "oss_bandwidth_on_demand",
    "oss_capex_record",
    "oss_infra_risk",
    "oss_ddos_attack",
    "oss_traffic_cost",
    "oss_iot_telemetry",
    "oss_mos_score",
    "oss_room_bandwidth",
    "oss_pms_property",
)


def upgrade() -> None:
    from app.database import Base
    import app.models  # noqa: F401
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    for t in BATCH3_TABLES:
        op.drop_table(t)
