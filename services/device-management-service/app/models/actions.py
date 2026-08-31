"""Governed device actions with authorization workflow."""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, Timestamped, UuidPk


class DeviceAction(Base, Timestamped, UuidPk):
    __tablename__ = "device_actions"
    __table_args__ = (Index("ix_device_action_state", "state"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device_tenants.id"), index=True, nullable=False)
    cpe_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("managed_cpes.id"), index=True, nullable=False)
    action_type: Mapped[str] = mapped_column(String(40), nullable=False)
    state: Mapped[str] = mapped_column(String(32), default="REQUESTED", nullable=False)
    parameters: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    requested_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    genieacs_task_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    connection_request_outcome: Mapped[str] = mapped_column(String(32), default="NOT_REQUESTED", nullable=False)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_summary: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)


class DeviceActionEvent(Base, Timestamped, UuidPk):
    __tablename__ = "device_action_events"
    __table_args__ = (Index("ix_device_action_event_action", "action_id"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device_tenants.id"), index=True, nullable=False)
    action_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device_actions.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
