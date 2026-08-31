"""Remote diagnostic jobs — capability-driven, governed, with normalized results."""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, Timestamped, UuidPk


class DiagnosticJob(Base, Timestamped, UuidPk):
    __tablename__ = "device_diagnostic_jobs"
    __table_args__ = (Index("ix_device_diag_state", "state"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device_tenants.id"), index=True, nullable=False)
    cpe_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("managed_cpes.id"), index=True, nullable=False)
    diagnostic_type: Mapped[str] = mapped_column(String(40), nullable=False)
    state: Mapped[str] = mapped_column(String(24), default="REQUESTED", nullable=False)
    customer_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    service_subscription_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    support_ticket_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    requested_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    input_parameters: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    capability_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    genieacs_task_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)


class DiagnosticResult(Base, Timestamped, UuidPk):
    __tablename__ = "device_diagnostic_results"
    __table_args__ = (Index("ix_device_diag_result_job", "job_id"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device_tenants.id"), index=True, nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device_diagnostic_jobs.id"), nullable=False)
    cpe_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("managed_cpes.id"), nullable=False)
    raw_result_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    normalized_result: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    units: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    evaluation: Mapped[str] = mapped_column(String(16), default="UNKNOWN", nullable=False)
    fault_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fault_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    offline: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
