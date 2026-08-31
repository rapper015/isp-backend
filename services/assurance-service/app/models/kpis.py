"""KPIs, synthetic checks, telemetry ingestion, metric registry, dashboards."""
import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, Timestamped, UuidPk


class KpiDefinition(Base, Timestamped, UuidPk):
    __tablename__ = "ass_kpi_definitions"
    __table_args__ = (UniqueConstraint("code", name="uq_ass_kpi_code"),)

    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    business_meaning: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    formula: Mapped[str] = mapped_column(Text, nullable=False)
    numerator: Mapped[str | None] = mapped_column(String(120), nullable=True)
    denominator: Mapped[str | None] = mapped_column(String(120), nullable=True)
    data_sources: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    dimensions: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    unit: Mapped[str] = mapped_column(String(24), default="number", nullable=False)
    freshness_seconds: Mapped[int] = mapped_column(Integer, default=900, nullable=False)
    validation_status: Mapped[str] = mapped_column(String(24), default="UNVALIDATED", nullable=False)


class KpiMeasurement(Base, Timestamped, UuidPk):
    __tablename__ = "ass_kpi_measurements"
    __table_args__ = (Index("ix_ass_kpi_measurement", "kpi_id", "period_key"),)

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    kpi_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    period_key: Mapped[str] = mapped_column(String(16), nullable=False)  # YYYY-MM-DD / YYYY-MM
    value: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    quality: Mapped[str] = mapped_column(String(16), default="FRESH", nullable=False)
    dimensions: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class KpiTarget(Base, Timestamped, UuidPk):
    __tablename__ = "ass_kpi_targets"
    __table_args__ = (UniqueConstraint("kpi_id", "target_key", name="uq_ass_kpi_target"),)

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    kpi_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    target_key: Mapped[str] = mapped_column(String(40), default="DEFAULT", nullable=False)
    target: Mapped[float] = mapped_column(Float, nullable=False)
    direction: Mapped[str] = mapped_column(String(8), default="ABOVE", nullable=False)  # ABOVE|BELOW


class SyntheticCheck(Base, Timestamped, UuidPk):
    __tablename__ = "ass_synthetic_checks"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_ass_synthetic_check"),)

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    target: Mapped[str | None] = mapped_column(String(255), nullable=True)
    frequency_seconds: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    tags: Mapped[list] = mapped_column(JSON, default=list, nullable=False)


class SyntheticResult(Base, Timestamped, UuidPk):
    __tablename__ = "ass_synthetic_results"
    __table_args__ = (Index("ix_ass_synthetic_result", "check_id", "checked_at"),)

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    check_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    result: Mapped[str] = mapped_column(String(16), default="PASS", nullable=False)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    detail: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class NetworkObservation(Base, Timestamped, UuidPk):
    __tablename__ = "ass_network_observations"
    __table_args__ = (Index("ix_ass_network_obs", "device_ref", "check_type", "observed_at"),)

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    device_ref: Mapped[str] = mapped_column(String(120), nullable=False)
    check_type: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="UNKNOWN", nullable=False)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(40), default="collector", nullable=False)


class MetricRegistry(Base, Timestamped, UuidPk):
    __tablename__ = "ass_metric_registry"
    __table_args__ = (UniqueConstraint("name", name="uq_ass_metric_registry"),)

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    metric_type: Mapped[str] = mapped_column(String(16), default="COUNTER", nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    safe_labels: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    owner: Mapped[str | None] = mapped_column(String(128), nullable=True)


class DashboardDefinition(Base, Timestamped, UuidPk):
    __tablename__ = "ass_dashboard_definitions"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_ass_dashboard_code"),)

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    purpose: Mapped[str | None] = mapped_column(Text, nullable=True)
    audience: Mapped[str] = mapped_column(String(40), default="NOC", nullable=False)
    data_source: Mapped[str] = mapped_column(String(120), default="grafana", nullable=False)
    refresh_interval: Mapped[str] = mapped_column(String(16), default="1m", nullable=False)
    tenant_scope: Mapped[str] = mapped_column(String(24), default="TENANT", nullable=False)
    review_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deprecation_status: Mapped[str] = mapped_column(String(24), default="ACTIVE", nullable=False)
    json_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
