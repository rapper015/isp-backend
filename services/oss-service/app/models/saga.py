"""Durable saga orchestration persistence: saga instances, steps, attempts,
workflow events and manual interventions. Saga state is persisted in the
database; RabbitMQ/Redis are never the only copy."""
import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Timestamped
from ..database import Base


class SagaInstance(Base, Timestamped):
    __tablename__ = "oss_saga_instances"
    __table_args__ = (Index("ix_oss_saga_tenant_order", "tenant_id", "order_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("oss_tenants.id"), index=True, nullable=False)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("oss_orders.id"), index=True, nullable=False)
    workflow_type: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[str] = mapped_column(String(32), default="PENDING", nullable=False)
    current_step_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SagaStep(Base, Timestamped):
    __tablename__ = "oss_saga_steps"
    __table_args__ = (UniqueConstraint("saga_id", "step_index", name="uq_oss_saga_step_index"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("oss_tenants.id"), index=True, nullable=False)
    saga_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("oss_saga_instances.id"), index=True, nullable=False)
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    step_name: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str] = mapped_column(String(16), default="PENDING", nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    output: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SagaStepAttempt(Base):
    __tablename__ = "oss_saga_step_attempts"
    __table_args__ = (Index("ix_oss_saga_attempt_step", "saga_step_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("oss_tenants.id"), index=True, nullable=False)
    saga_step_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("oss_saga_steps.id"), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(String(500), nullable=True)
    output: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkflowEvent(Base):
    """Audit trail of saga progress, decisions and compensations."""
    __tablename__ = "oss_workflow_events"
    __table_args__ = (Index("ix_oss_workflow_event_saga", "saga_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    saga_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("oss_saga_instances.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    step_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ManualIntervention(Base, Timestamped):
    __tablename__ = "oss_manual_interventions"
    __table_args__ = (Index("ix_oss_intervention_tenant_order", "tenant_id", "order_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("oss_tenants.id"), index=True, nullable=False)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("oss_orders.id"), nullable=False)
    saga_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("oss_saga_instances.id"), nullable=True)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="OPEN", nullable=False)
    resolved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
