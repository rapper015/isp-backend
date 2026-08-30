"""OSS asset, config, vendor, enterprise, infra, security models (Batch 3).

Coverage: 205 vendor mgmt, 208 firmware, 231 splitter hierarchy, 246 config push,
248 config drift, 673 VPN, 675 bandwidth-on-demand, 676 SLA contracts, 707 IoT
telemetry, 717 PMS, 722 room bandwidth, 728 MOS, 1013 inventory drift, 1138 site
ownership, 1143 CapEx, 1145-1147 vendor SLA/performance/penalties, 1208 DDoS,
1254 traffic cost, 1462 infra risk heatmap.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Timestamped
from ..database import Base


def _now():
    return datetime.now(timezone.utc)


class Vendor(Base, Timestamped):
    __tablename__ = "oss_vendor"
    __table_args__ = (Index("ix_oss_vendor_tenant", "tenant_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("oss_tenants.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    contact: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    sla_minutes: Mapped[int] = mapped_column(Integer, default=480)
    penalty_amount: Mapped[float] = mapped_column(Float, default=0.0)
    breaches: Mapped[int] = mapped_column(Integer, default=0)
    performance_score: Mapped[float] = mapped_column(Float, default=100.0)
    last_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NetworkAsset(Base, Timestamped):
    __tablename__ = "oss_network_asset"
    __table_args__ = (Index("ix_oss_asset_tenant_type", "tenant_id", "asset_type"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("oss_tenants.id"), index=True, nullable=False)
    asset_type: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("oss_vendor.id"))
    model: Mapped[str | None] = mapped_column(String(120))
    serial_number: Mapped[str | None] = mapped_column(String(120))
    firmware_version: Mapped[str | None] = mapped_column(String(60))
    site_owner: Mapped[str | None] = mapped_column(String(200))
    location: Mapped[str | None] = mapped_column(String(300))
    category: Mapped[str | None] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    last_config_hash: Mapped[str | None] = mapped_column(String(64))


class FirmwareLog(Base, Timestamped):
    __tablename__ = "oss_firmware_log"
    __table_args__ = (Index("ix_oss_firmware_asset", "asset_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("oss_tenants.id"), index=True, nullable=False)
    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("oss_network_asset.id"), nullable=False)
    from_version: Mapped[str | None] = mapped_column(String(60))
    to_version: Mapped[str] = mapped_column(String(60))
    applied_by: Mapped[str | None] = mapped_column(String(200))
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class SplitterNode(Base, Timestamped):
    __tablename__ = "oss_splitter_node"
    __table_args__ = (Index("ix_oss_splitter_tenant", "tenant_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("oss_tenants.id"), index=True, nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("oss_splitter_node.id"))
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    location: Mapped[str | None] = mapped_column(String(300))
    level: Mapped[int] = mapped_column(Integer, default=1)
    ports_used: Mapped[int] = mapped_column(Integer, default=0)
    ports_total: Mapped[int] = mapped_column(Integer, default=16)


class ConfigSnapshot(Base, Timestamped):
    __tablename__ = "oss_config_snapshot"
    __table_args__ = (Index("ix_oss_cfg_snapshot_asset", "asset_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("oss_tenants.id"), index=True, nullable=False)
    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("oss_network_asset.id"), nullable=False)
    config_text: Mapped[str] = mapped_column(Text)
    config_hash: Mapped[str] = mapped_column(String(64))
    is_baseline: Mapped[bool] = mapped_column(default=False)
    drift: Mapped[bool] = mapped_column(default=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ConfigPushRequest(Base, Timestamped):
    __tablename__ = "oss_config_push"
    __table_args__ = (Index("ix_oss_cfg_push_asset", "asset_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("oss_tenants.id"), index=True, nullable=False)
    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("oss_network_asset.id"), nullable=False)
    config_text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    pushed_by: Mapped[str | None] = mapped_column(String(200))
    pushed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EnterpriseSLA(Base, Timestamped):
    __tablename__ = "oss_enterprise_sla"
    __table_args__ = (Index("ix_oss_esla_tenant", "tenant_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("oss_tenants.id"), index=True, nullable=False)
    customer_id: Mapped[str] = mapped_column(String(120), nullable=False)
    terms: Mapped[dict] = mapped_column(JSON, default=dict)  # availability_pct, response_minutes
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    signed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class VPNService(Base, Timestamped):
    __tablename__ = "oss_vpn_service"
    __table_args__ = (Index("ix_oss_vpn_tenant", "tenant_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("oss_tenants.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    customer_id: Mapped[str] = mapped_column(String(120), nullable=False)
    vpn_type: Mapped[str] = mapped_column(String(20), default="IPSEC")  # IPSEC | MPLS
    endpoints: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")


class BandwidthOnDemand(Base, Timestamped):
    __tablename__ = "oss_bandwidth_on_demand"
    __table_args__ = (Index("ix_oss_bod_tenant", "tenant_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("oss_tenants.id"), index=True, nullable=False)
    subscription_id: Mapped[str] = mapped_column(String(120), nullable=False)
    boost_mbps: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=60)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CapExRecord(Base, Timestamped):
    __tablename__ = "oss_capex_record"
    __table_args__ = (Index("ix_oss_capex_tenant", "tenant_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("oss_tenants.id"), index=True, nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    area: Mapped[str | None] = mapped_column(String(200))
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="INR")
    note: Mapped[str | None] = mapped_column(Text)
    booked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class InfraRisk(Base, Timestamped):
    __tablename__ = "oss_infra_risk"
    __table_args__ = (Index("ix_oss_risk_tenant", "tenant_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("oss_tenants.id"), index=True, nullable=False)
    scope: Mapped[str] = mapped_column(String(200), nullable=False)  # area or asset
    risk_score: Mapped[float] = mapped_column(Float, nullable=False)
    level: Mapped[str] = mapped_column(String(20), default="LOW")
    factors: Mapped[dict] = mapped_column(JSON, default=dict)
    assessed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class DDoSAttack(Base, Timestamped):
    __tablename__ = "oss_ddos_attack"
    __table_args__ = (Index("ix_oss_ddos_tenant", "tenant_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("oss_tenants.id"), index=True, nullable=False)
    target: Mapped[str] = mapped_column(String(200), nullable=False)
    vector: Mapped[str | None] = mapped_column(String(80))
    volume_mbps: Mapped[float] = mapped_column(Float, default=0.0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="OPEN")  # OPEN | MITIGATED


class TrafficCost(Base, Timestamped):
    __tablename__ = "oss_traffic_cost"
    __table_args__ = (Index("ix_oss_traffic_tenant", "tenant_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("oss_tenants.id"), index=True, nullable=False)
    route: Mapped[str] = mapped_column(String(200), nullable=False)
    direction: Mapped[str] = mapped_column(String(20), default="UPSTREAM")
    volume_gb: Mapped[float] = mapped_column(Float, default=0.0)
    cost: Mapped[float] = mapped_column(Float, default=0.0)
    period: Mapped[str] = mapped_column(String(20), default="MONTH")


class IoTDeviceTelemetry(Base, Timestamped):
    __tablename__ = "oss_iot_telemetry"
    __table_args__ = (Index("ix_oss_iot_tenant", "tenant_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("oss_tenants.id"), index=True, nullable=False)
    device_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    metric: Mapped[str] = mapped_column(String(80), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class MOSScore(Base, Timestamped):
    __tablename__ = "oss_mos_score"
    __table_args__ = (Index("ix_oss_mos_tenant", "tenant_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("oss_tenants.id"), index=True, nullable=False)
    session_id: Mapped[str] = mapped_column(String(120), index=True)
    subscriber_id: Mapped[str | None] = mapped_column(String(120))
    score: Mapped[float] = mapped_column(Float, nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class RoomBandwidth(Base, Timestamped):
    __tablename__ = "oss_room_bandwidth"
    __table_args__ = (Index("ix_oss_room_tenant", "tenant_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("oss_tenants.id"), index=True, nullable=False)
    room_number: Mapped[str] = mapped_column(String(60), nullable=False)
    property: Mapped[str | None] = mapped_column(String(160))
    plan_mbps: Mapped[int] = mapped_column(Integer, default=50)
    applied_mbps: Mapped[int] = mapped_column(Integer, default=50)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")


class PMSProperty(Base, Timestamped):
    __tablename__ = "oss_pms_property"
    __table_args__ = (Index("ix_oss_pms_tenant", "tenant_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("oss_tenants.id"), index=True, nullable=False)
    property_name: Mapped[str] = mapped_column(String(160), nullable=False)
    pms_system: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20), default="CONNECTED")
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
