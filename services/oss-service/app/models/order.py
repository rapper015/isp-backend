"""Event-sourced order aggregate: the Order (current state + requested snapshot),
the immutable OrderEvent stream, status history and command deduplication."""
import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Timestamped
from ..database import Base


class Order(Base, Timestamped):
    __tablename__ = "oss_orders"
    __table_args__ = (UniqueConstraint("tenant_id", "order_number", name="uq_oss_order_tenant_number"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("oss_tenants.id"), index=True, nullable=False)
    order_number: Mapped[str] = mapped_column(String(64), nullable=False)
    order_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(32), default="DRAFT", nullable=False, index=True)
    aggregate_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # External references (immutable IDs across bounded contexts; strings).
    customer_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    service_subscription_id: Mapped[uuid.UUID | None] = mapped_column(index=True, nullable=True)
    service_location_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    requested_plan_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    previous_plan_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    requested_activation_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    priority: Mapped[str] = mapped_column(String(16), default="MEDIUM", nullable=False)
    source_channel: Mapped[str] = mapped_column(String(32), default="API", nullable=False)
    franchise_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    reseller_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    current_step: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    # Immutable snapshot of essential requested input (historical integrity).
    requested_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    submitted_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OrderEvent(Base):
    """Immutable, append-only event stream. Corrected events are new events."""
    __tablename__ = "oss_order_events"
    __table_args__ = (
        UniqueConstraint("order_id", "aggregate_version", name="uq_oss_order_event_version"),
        Index("ix_oss_order_event_order", "order_id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("oss_tenants.id"), index=True, nullable=False)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("oss_orders.id"), nullable=False)
    aggregate_version: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    event_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    actor_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    actor_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    causation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    event_metadata: Mapped[dict] = mapped_column("metadata", JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class OrderStatusHistory(Base):
    __tablename__ = "oss_order_status_history"
    __table_args__ = (Index("ix_oss_order_history_order", "order_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("oss_tenants.id"), index=True, nullable=False)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("oss_orders.id"), nullable=False)
    from_state: Mapped[str] = mapped_column(String(32), nullable=False)
    to_state: Mapped[str] = mapped_column(String(32), nullable=False)
    actor: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class OrderCommand(Base, Timestamped):
    __tablename__ = "oss_order_commands"
    __table_args__ = (UniqueConstraint("tenant_id", "idempotency_key", name="uq_oss_order_command_idempotency"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("oss_tenants.id"), index=True, nullable=False)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("oss_orders.id"), index=True, nullable=False)
    command: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="QUEUED", nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    result: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
