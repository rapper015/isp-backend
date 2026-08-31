"""Managed CPE identity, onboarding/claiming, relationships, ownership history,
secret references and normalized telemetry."""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, Timestamped, UuidPk


class ManagedCpe(Base, Timestamped, UuidPk):
    """The remotely managed TR-069 device identity and operational state.

    Primary ACS identity is the (OUI, product class, serial) tuple. MACs and
    display names are tracked but never used as the primary identity."""

    __tablename__ = "managed_cpes"
    __table_args__ = (
        UniqueConstraint("tenant_id", "oui", "product_class", "serial_number", name="uq_device_acs_identity"),
        Index("ix_device_cpe_serial", "serial_number"),
        Index("ix_device_cpe_state", "state"),
    )

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("device_tenants.id"), index=True, nullable=True)
    # ACS identity tuple
    oui: Mapped[str] = mapped_column(String(8), nullable=False)
    product_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    serial_number: Mapped[str] = mapped_column(String(128), nullable=False)
    aggregate_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Tracking attributes
    manufacturer: Mapped[str | None] = mapped_column(String(128), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    hardware_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    firmware_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    wan_mac: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    lan_mac: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    data_model_family: Mapped[str] = mapped_column(String(24), default="UNKNOWN", nullable=False)
    data_model_version: Mapped[str] = mapped_column(String(16), default="UNKNOWN", nullable=False)
    acs_device_id: Mapped[str | None] = mapped_column(String(255), index=True, nullable=True)
    acs_instance_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("acs_instances.id"), nullable=True)
    connection_request_url_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # State
    state: Mapped[str] = mapped_column(String(24), default="DISCOVERED", nullable=False, index=True)
    operational_status: Mapped[str] = mapped_column(String(24), default="UNKNOWN", nullable=False)
    online: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Business relationships
    inventory_asset_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    inventory_serial: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    customer_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    service_subscription_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    service_location_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    oss_order_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    work_order_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    support_ticket_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    # Timestamps
    first_inform_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_inform_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_boot_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_bootstrap_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    periodic_inform_interval: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Configuration / firmware compliance
    current_profile_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("device_configuration_profile_versions.id"), nullable=True)
    profile_compliance: Mapped[str] = mapped_column(String(24), default="UNKNOWN", nullable=False)
    firmware_compliance: Mapped[str] = mapped_column(String(24), default="UNKNOWN", nullable=False)
    last_drift_classification: Mapped[str] = mapped_column(String(32), default="NONE", nullable=False)
    # Device model linkage
    model_variant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("device_model_variants.id"), nullable=True)
    # Capability snapshot id
    capability_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    # Metadata
    display_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    claimed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class CpeOnboarding(Base, Timestamped, UuidPk):
    """Tenant-resolution record for a discovered/claimed device."""

    __tablename__ = "device_cpe_onboarding"

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("device_tenants.id"), index=True, nullable=True)
    cpe_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("managed_cpes.id"), index=True, nullable=False)
    resolution_method: Mapped[str] = mapped_column(String(40), nullable=False)
    evidence: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    result: Mapped[str] = mapped_column(String(16), default="UNKNOWN", nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    actor: Mapped[str | None] = mapped_column(String(128), nullable=True)
    conflicting_matches: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    resolved_tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class CpeRelationship(Base, Timestamped, UuidPk):
    """Immutable device ↔ business-entity relationship history."""

    __tablename__ = "device_cpe_relationships"
    __table_args__ = (Index("ix_device_relationship_cpe", "cpe_id"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device_tenants.id"), index=True, nullable=False)
    cpe_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("managed_cpes.id"), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)  # CUSTOMER / SERVICE / LOCATION / ORDER / ...
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    relationship: Mapped[str] = mapped_column(String(32), default="ASSIGNED", nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    assigned_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)


class CpeOwnershipHistory(Base, Timestamped, UuidPk):
    __tablename__ = "device_cpe_ownership_history"
    __table_args__ = (Index("ix_device_ownership_cpe", "cpe_id"),)

    cpe_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("managed_cpes.id"), nullable=False)
    from_tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    to_tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    transfer_type: Mapped[str] = mapped_column(String(32), default="CLAIM", nullable=False)  # CLAIM / TRANSFER / REPLACEMENT
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    actor: Mapped[str | None] = mapped_column(String(128), nullable=True)
    previous_cpe_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    replacement_cpe_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)


class CpeSecretReference(Base, Timestamped, UuidPk):
    """Encrypted secret reference for a device. Raw secrets are never stored or
    logged; APIs return masked metadata only."""

    __tablename__ = "device_cpe_secrets"
    __table_args__ = (UniqueConstraint("cpe_id", "kind", name="uq_device_cpe_secret"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device_tenants.id"), index=True, nullable=False)
    cpe_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("managed_cpes.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)  # CWMP / PPPOE / WIFI / CONNECTION_REQUEST / SIP / ADMIN
    secret_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    key_alias: Mapped[str] = mapped_column(String(64), default="default", nullable=False)
    masked_value: Mapped[str] = mapped_column(String(64), default="••••", nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CpeTelemetry(Base, Timestamped, UuidPk):
    """Normalized business-relevant telemetry snapshot. Raw ACS parameter tree
    stays in GenieACS; only normalized signals are stored, with retention."""

    __tablename__ = "device_cpe_telemetry"
    __table_args__ = (Index("ix_device_telemetry_cpe_time", "cpe_id", "captured_at"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device_tenants.id"), index=True, nullable=False)
    cpe_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("managed_cpes.id"), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    online: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    wan_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    wan_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    ppp_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    ppp_username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    wifi_state: Mapped[str | None] = mapped_column(String(24), nullable=True)
    connected_host_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    optical_rx_dbm: Mapped[float | None] = mapped_column(Float, nullable=True)
    optical_tx_dbm: Mapped[float | None] = mapped_column(Float, nullable=True)
    uptime_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cpu_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    memory_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    temperature_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    firmware_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    active_fault_summary: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    retention_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CpeCapabilitySnapshot(Base, Timestamped, UuidPk):
    """Stored capability snapshot for a managed CPE after discovery/firmware change."""

    __tablename__ = "device_cpe_capability_snapshots"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device_tenants.id"), index=True, nullable=False)
    cpe_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("managed_cpes.id"), index=True, nullable=False)
    state: Mapped[str] = mapped_column(String(32), default="UNVERIFIED", nullable=False)
    parameters: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    diagnostics: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    actions: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    writable_parameters: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    firmware_operations: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    mapping_drift: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    captured_after_firmware: Mapped[str | None] = mapped_column(String(64), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class CpeEvent(Base, Timestamped):
    """Immutable device timeline event (aggregate-versioned)."""

    __tablename__ = "device_cpe_events"
    __table_args__ = (
        UniqueConstraint("cpe_id", "version", name="uq_device_cpe_event_version"),
        Index("ix_device_cpe_event_type", "event_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    cpe_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("managed_cpes.id"), nullable=False)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("device_tenants.id"), index=True, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    actor_type: Mapped[str] = mapped_column(String(24), default="system", nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    causation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
