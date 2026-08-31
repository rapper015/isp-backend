"""Resource inventory and reservation ledger.

Authoritative resource ownership lives with IPAM/Network Inventory; OSS
coordinates reservations through the ResourceProvider adapter. The inventory
table mirrors available resources for deterministic, conflict-free allocation
with database compare-and-set semantics.
"""
import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Timestamped
from ..database import Base


class ResourceInventory(Base, Timestamped):
    """A concrete allocatable resource (IP, VLAN, port, ONT, NAS, ...)."""
    __tablename__ = "oss_resource_inventory"
    __table_args__ = (
        UniqueConstraint("tenant_id", "resource_type", "resource_key", name="uq_oss_resource_key"),
        Index("ix_oss_resource_tenant_type", "tenant_id", "resource_type"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("oss_tenants.id"), index=True, nullable=False)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_key: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="AVAILABLE", nullable=False)
    reservation_token: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    extra_metadata: Mapped[dict] = mapped_column("metadata", JSON, default=dict, nullable=False)


class ResourceReservation(Base):
    """Append-only reservation ledger entry."""
    __tablename__ = "oss_resource_reservations"
    __table_args__ = (Index("ix_oss_reservation_tenant_order", "tenant_id", "order_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("oss_tenants.id"), index=True, nullable=False)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_key: Mapped[str] = mapped_column(String(255), nullable=False)
    order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("oss_orders.id"), nullable=True, index=True)
    service_subscription_id: Mapped[uuid.UUID | None] = mapped_column(index=True, nullable=True)
    reservation_token: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    reserved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    allocated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    actor: Mapped[str | None] = mapped_column(String(128), nullable=True)
