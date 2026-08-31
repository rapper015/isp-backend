"""ACS instance management: registered GenieACS instances, health, bindings,
credentials (encrypted references) and capabilities. Moving a device between
ACS instances requires a controlled migration workflow."""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, Timestamped, UuidPk


class ACSInstance(Base, Timestamped, UuidPk):
    __tablename__ = "acs_instances"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_acs_instance_name"),)

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("device_tenants.id"), index=True, nullable=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    base_url: Mapped[str] = mapped_column(String(255), nullable=False)
    environment: Mapped[str] = mapped_column(String(16), default="PRODUCTION", nullable=False)
    cwmp_endpoint: Mapped[str | None] = mapped_column(String(255), nullable=True)
    file_service_endpoint: Mapped[str | None] = mapped_column(String(255), nullable=True)
    api_auth_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)  # encrypted secret reference
    version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    health: Mapped[str] = mapped_column(String(24), default="UNKNOWN", nullable=False)
    last_health_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    capacity_metadata: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class ACSHealth(Base, Timestamped, UuidPk):
    __tablename__ = "acs_health"

    instance_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("acs_instances.id"), index=True, nullable=False)
    state: Mapped[str] = mapped_column(String(24), default="UNKNOWN", nullable=False)
    version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detail: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class ACSDeviceBinding(Base, Timestamped, UuidPk):
    """Links a managed CPE to its GenieACS device record on a specific ACS instance."""

    __tablename__ = "acs_device_bindings"
    __table_args__ = (
        UniqueConstraint("instance_id", "acs_device_id", name="uq_acs_binding_device"),
        Index("ix_acs_binding_cpe", "cpe_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device_tenants.id"), index=True, nullable=False)
    instance_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("acs_instances.id"), nullable=False)
    cpe_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("managed_cpes.id"), nullable=False)
    acs_device_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class ACSCapability(Base, Timestamped, UuidPk):
    __tablename__ = "acs_capabilities"
    __table_args__ = (UniqueConstraint("instance_id", "name", name="uq_acs_capability"),)

    instance_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("acs_instances.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    supported: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    detail: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class ACSInstanceCredential(Base, Timestamped, UuidPk):
    """Encrypted secret reference for ACS API auth. Raw secrets are never stored."""

    __tablename__ = "acs_instance_credentials"

    instance_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("acs_instances.id"), index=True, nullable=False)
    secret_ref: Mapped[str] = mapped_column(String(128), nullable=False)  # encrypted blob reference
    key_alias: Mapped[str] = mapped_column(String(64), default="acs_api", nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
