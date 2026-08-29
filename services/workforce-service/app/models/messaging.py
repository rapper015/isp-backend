"""Transactional outbox, consumer inbox (idempotency), tenant reference and an
immutable administrative audit log.

Every cross-service event the workforce service publishes goes through the
outbox; every inbound event is deduplicated through the inbox. Redis is never
authoritative for either."""
import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base


class Tenant(Base):
    __tablename__ = "workforce_tenants"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class OutboxEvent(Base):
    __tablename__ = "workforce_outbox_events"
    __table_args__ = (Index("ix_workforce_outbox_published", "published_at"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(default=0, nullable=False)


class InboxMessage(Base):
    """Deduplicates inbound events; consumer inbox pattern."""
    __tablename__ = "workforce_inbox_messages"
    __table_args__ = (Index("ix_workforce_inbox_consumer", "consumer"),)
    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    consumer: Mapped[str] = mapped_column(String(128), primary_key=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AuditLog(Base):
    """Immutable administrative audit for configuration/governance changes
    (templates, service areas, SLA policies, technician overrides)."""
    __tablename__ = "workforce_audit_log"
    __table_args__ = (Index("ix_workforce_audit_tenant", "tenant_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actor: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    safe_before: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    safe_after: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
