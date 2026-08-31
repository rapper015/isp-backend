"""Tenant, outbox, inbox and audit primitives for the device-management service."""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, Timestamped


class Tenant(Base):
    __tablename__ = "device_tenants"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)


class OutboxEvent(Base):
    __tablename__ = "device_outbox_events"
    __table_args__ = (Index("ix_device_outbox_published", "published_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    causation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(default=0, nullable=False)


class InboxMessage(Base):
    """Deduplicates inbound events; consumer inbox pattern."""

    __tablename__ = "device_inbox_messages"
    __table_args__ = (Index("ix_device_inbox_consumer", "consumer"),)

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    consumer: Mapped[str] = mapped_column(String(128), primary_key=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AuditLog(Base):
    """Immutable administrative audit for governance/security-sensitive changes."""

    __tablename__ = "device_audit_log"
    __table_args__ = (Index("ix_device_audit_tenant", "tenant_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    actor: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
