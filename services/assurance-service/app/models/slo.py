"""SLI/SLO definitions, measurements, error budgets, maintenance windows."""
import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, Timestamped, UuidPk


class SlIDefinition(Base, Timestamped, UuidPk):
    __tablename__ = "ass_sli_definitions"
    __table_args__ = (UniqueConstraint("code", name="uq_ass_sli_code"),)

    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    service_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    measurement_source: Mapped[str] = mapped_column(String(120), default="collector", nullable=False)
    good_event_definition: Mapped[str] = mapped_column(Text, nullable=False)
    valid_event_definition: Mapped[str] = mapped_column(Text, nullable=False)
    query_expression: Mapped[str | None] = mapped_column(Text, nullable=True)
    unit: Mapped[str] = mapped_column(String(24), default="ratio", nullable=False)
    exclusions: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    validation_status: Mapped[str] = mapped_column(String(24), default="UNVALIDATED", nullable=False)


class SlIMeasurement(Base, Timestamped, UuidPk):
    __tablename__ = "ass_sli_measurements"
    __table_args__ = (Index("ix_ass_sli_measurement_window", "sli_id", "window_start", "window_end"),)

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    sli_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    good: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    total: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    quality: Mapped[str] = mapped_column(String(16), default="VALID", nullable=False)
    excluded_good: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    excluded_total: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SloDefinition(Base, Timestamped, UuidPk):
    __tablename__ = "ass_slo_definitions"
    __table_args__ = (Index("ix_ass_slo_service", "service_id"),)

    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    service_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    sli_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(24), default="DRAFT", nullable=False)
    owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SloVersion(Base, Timestamped, UuidPk):
    """Immutable published SLO version. Published versions are never edited."""

    __tablename__ = "ass_slo_versions"
    __table_args__ = (UniqueConstraint("slo_id", "version", name="uq_ass_slo_version"),)

    slo_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    objective: Mapped[float] = mapped_column(Float, nullable=False)
    window_type: Mapped[str] = mapped_column(String(16), default="ROLLING", nullable=False)
    window_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    service_tier: Mapped[str] = mapped_column(String(16), default="STANDARD", nullable=False)
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_budget_policy: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    alert_thresholds: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    state: Mapped[str] = mapped_column(String(24), default="DRAFT", nullable=False)
    changed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class SloWindowState(Base, Timestamped, UuidPk):
    """Computed SLO window: reproducible inputs + results + policy version."""

    __tablename__ = "ass_slo_window_states"
    __table_args__ = (UniqueConstraint("slo_id", "window_start", "window_end", name="uq_ass_slo_window"),)

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    slo_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    good: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    total: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    sli_ratio: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    objective: Mapped[float] = mapped_column(Float, nullable=False)
    allowed_bad: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    consumed_bad: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    remaining_budget: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    burn_rate: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="HEALTHY", nullable=False)
    fast_burn: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    slow_burn: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MaintenanceWindow(Base, Timestamped, UuidPk):
    __tablename__ = "ass_maintenance_windows"
    __table_args__ = (Index("ix_ass_mw_scope", "tenant_id", "service_id"),)

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    service_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    scope_kind: Mapped[str] = mapped_column(String(24), default="SERVICE", nullable=False)
    scope_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    maintenance_type: Mapped[str] = mapped_column(String(16), default="PLANNED", nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    state: Mapped[str] = mapped_column(String(24), default="REQUESTED", nullable=False)
    sla_treatment: Mapped[str] = mapped_column(String(24), default="EXCLUDE", nullable=False)
    alert_suppression: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class MaintenanceException(Base, Timestamped, UuidPk):
    __tablename__ = "ass_maintenance_exceptions"
    __table_args__ = (UniqueConstraint("maintenance_id", "slo_id", name="uq_ass_maintenance_exception"),)

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    maintenance_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    slo_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    approved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
