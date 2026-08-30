import uuid
from datetime import datetime
from sqlalchemy import Boolean, DateTime, Float, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from .database import Base


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class NasDevice(Base):
    __tablename__ = 'nas_devices'
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    host: Mapped[str] = mapped_column(String(255), unique=True)
    status: Mapped[str] = mapped_column(String(16), default='unknown')
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

class HealthObservation(Base):
    __tablename__ = 'health_observations'
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    nas_id: Mapped[uuid.UUID] = mapped_column(index=True)
    status: Mapped[str] = mapped_column(String(16))
    detail: Mapped[str | None] = mapped_column(String(255), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# --- Master Spec Batch 7c: NMS operations (tenant-scoped) ---

class EscalationPolicy(Base, Timestamped):
    __tablename__ = "nms_escalation_policy"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_nms_esc_policy"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    rule_json: Mapped[dict] = mapped_column(JSON, default=dict)  # {"severity": "HIGH", "steps": [...]}
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class ConfigSnapshot(Base, Timestamped):
    __tablename__ = "nms_config_snapshot"
    __table_args__ = (UniqueConstraint("tenant_id", "device_id", "label", name="uq_nms_cfg_snapshot"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True, nullable=False)
    device_id: Mapped[str] = mapped_column(String(128), nullable=False)
    label: Mapped[str] = mapped_column(String(60), nullable=False)  # BASELINE | CURRENT
    config_text: Mapped[str] = mapped_column(Text, nullable=False)


class ApprovalSla(Base, Timestamped):
    __tablename__ = "nms_approval_sla"
    __table_args__ = (UniqueConstraint("tenant_id", "approval_type", name="uq_nms_approval_sla"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True, nullable=False)
    approval_type: Mapped[str] = mapped_column(String(120), nullable=False)
    sla_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    overdue_count: Mapped[int] = mapped_column(Integer, default=0)


class CacheStrategy(Base, Timestamped):
    __tablename__ = "nms_cache_strategy"
    __table_args__ = (UniqueConstraint("tenant_id", "cache_key", name="uq_nms_cache_strategy"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True, nullable=False)
    cache_key: Mapped[str] = mapped_column(String(160), nullable=False)
    ttl_seconds: Mapped[int] = mapped_column(Integer, default=300)
    strategy: Mapped[str] = mapped_column(String(30), default="LRU")


class DegradationRule(Base, Timestamped):
    __tablename__ = "nms_degradation_rule"
    __table_args__ = (UniqueConstraint("tenant_id", "service", name="uq_nms_degradation"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True, nullable=False)
    service: Mapped[str] = mapped_column(String(120), nullable=False)
    degraded_mode: Mapped[str] = mapped_column(String(40), default="REDUCE_CONCURRENCY")
    keep_alive_pct: Mapped[float] = mapped_column(Float, default=50.0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class QueueSaturation(Base, Timestamped):
    __tablename__ = "nms_queue_saturation"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True, nullable=False)
    queue: Mapped[str] = mapped_column(String(120), nullable=False)
    depth: Mapped[int] = mapped_column(Integer, default=0)
    max_depth: Mapped[int] = mapped_column(Integer, default=1000)
    protected: Mapped[bool] = mapped_column(Boolean, default=False)


# --- Master Spec Batch 8d: runbook automation + anomaly heatmaps ---

class Runbook(Base, Timestamped):
    """Runbook Automation (feature 284): predefined incident workflows."""
    __tablename__ = "nms_runbook"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_nms_runbook"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    trigger: Mapped[str] = mapped_column(String(120), nullable=False)  # event type / severity
    steps: Mapped[list] = mapped_column(JSON, default=list)
    executions: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")


class AnomalyHeatmap(Base, Timestamped):
    """Anomaly Heatmaps (feature 743): visual anomaly clusters by region/service."""
    __tablename__ = "nms_anomaly_heatmap"
    __table_args__ = (UniqueConstraint("tenant_id", "period", "scope", name="uq_nms_heatmap"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True, nullable=False)
    period: Mapped[str] = mapped_column(String(20), default="DAY")
    scope: Mapped[str] = mapped_column(String(120), nullable=False)  # region | service | device group
    cells: Mapped[list] = mapped_column(JSON, default=list)  # [{"key": "MH-GW1", "severity": 0.8, "count": 12}]
    anomaly_count: Mapped[int] = mapped_column(Integer, default=0)
