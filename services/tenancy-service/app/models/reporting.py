"""Tenant-aware reports, authorized platform aggregates and export records."""
import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, Timestamped, UuidPk


class ReportSnapshot(Base, Timestamped, UuidPk):
    """A materialized tenant/franchise report, protected by an authorized scope."""

    __tablename__ = "ten_report_snapshots"
    __table_args__ = (Index("ix_ten_report_tenant", "tenant_id"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    scope_kind: Mapped[str] = mapped_column(String(24), nullable=False)  # TENANT|FRANCHISE|BRANCH|ORG_UNIT
    scope_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    report_type: Mapped[str] = mapped_column(String(64), nullable=False)
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    generated_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_aggregate: Mapped[bool] = mapped_column(default=False, nullable=False)


class AggregateProjection(Base, Timestamped, UuidPk):
    """Authorized platform-wide aggregate (privacy-preserving dimensions only).
    One row per source tenant; platform queries sum across tenant rows."""

    __tablename__ = "ten_aggregate_projections"
    __table_args__ = (UniqueConstraint("metric", "dimension", "period_key", "source_tenant_id",
                                       name="uq_ten_aggregate_projection"),)

    metric: Mapped[str] = mapped_column(String(64), nullable=False)
    dimension: Mapped[str] = mapped_column(String(64), default="tenant", nullable=False)
    period_key: Mapped[str] = mapped_column(String(16), nullable=False)  # YYYY-MM
    value: Mapped[float] = mapped_column(default=0.0, nullable=False)
    source_tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    freshness_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ExportJob(Base, Timestamped, UuidPk):
    __tablename__ = "ten_export_jobs"
    __table_args__ = (Index("ix_ten_export_tenant", "tenant_id"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    scope_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    scope_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    export_type: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(24), default="QUEUED", nullable=False)
    download_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    requested_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    failure_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
