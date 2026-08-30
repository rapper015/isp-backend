"""Persistent AAA state. PostgreSQL is authoritative; caches are never authoritative."""
import uuid
from datetime import datetime
from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .database import Base

class Timestamped:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

class Tenant(Base, Timestamped):
    __tablename__ = "aaa_tenants"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    policy: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

class Credential(Base, Timestamped):
    __tablename__ = "aaa_credentials"
    __table_args__ = (UniqueConstraint("tenant_id", "username_normalized", name="uq_credential_tenant_username"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("aaa_tenants.id"), index=True, nullable=False)
    subscriber_id: Mapped[uuid.UUID] = mapped_column(index=True)
    username: Mapped[str] = mapped_column(String(128), nullable=False)
    username_normalized: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    encrypted_protocol_secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    protocol_secret_key_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    credential_type: Mapped[str] = mapped_column(String(32), default="pap", nullable=False)
    allowed_methods: Mapped[list] = mapped_column(JSON, default=lambda: ["pap"], nullable=False)
    mac_address: Mapped[str | None] = mapped_column(String(17), nullable=True, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)

class RadiusServerGroup(Base, Timestamped):
    __tablename__ = "radius_server_groups"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("aaa_tenants.id"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    region: Mapped[str | None] = mapped_column(String(64), nullable=True)
    minimum_healthy: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    failover_policy: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

class Nas(Base, Timestamped):
    """Canonical NAS record. Management and RADIUS source addresses are distinct.

    ``source_ip`` is the address the router uses to send RADIUS packets.
    ``management_host`` is the address the backend uses to reach the router.
    """
    __tablename__ = "aaa_nas"
    __table_args__ = (UniqueConstraint("tenant_id", "source_ip", name="uq_nas_tenant_source_ip"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("aaa_tenants.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    short_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    site: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Management connectivity (backend -> router)
    management_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    management_port: Mapped[int] = mapped_column(Integer, default=8729, nullable=False)
    management_protocol: Mapped[str] = mapped_column(String(16), default="api_ssl", nullable=False)
    api_mode: Mapped[str] = mapped_column(String(16), default="auto", nullable=False)
    tls_verify: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # RADIUS source (router -> FreeRADIUS)
    source_ip: Mapped[str] = mapped_column(String(45), nullable=False, index=True)
    source_cidr: Mapped[str | None] = mapped_column(String(50), nullable=True)
    radius_source_ipv6: Mapped[str | None] = mapped_column(String(45), nullable=True)
    nas_identifier: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    vendor: Mapped[str] = mapped_column(String(64), default="generic", nullable=False)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    serial_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    device_type: Mapped[str] = mapped_column(String(64), default="router", nullable=False)
    routeros_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    architecture: Mapped[str | None] = mapped_column(String(32), nullable=True)
    board_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    identity: Mapped[str | None] = mapped_column(String(64), nullable=True)
    time_zone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    auth_port: Mapped[int] = mapped_column(Integer, default=1812, nullable=False)
    accounting_port: Mapped[int] = mapped_column(Integer, default=1813, nullable=False)
    coa_port: Mapped[int] = mapped_column(Integer, default=3799, nullable=False)
    secret_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    secret_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    allowed_services: Mapped[list] = mapped_column(JSON, default=lambda: ["pppoe", "hotspot"], nullable=False)
    allowed_methods: Mapped[list] = mapped_column(JSON, default=lambda: ["pap"], nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    radius_group_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("radius_server_groups.id"), nullable=True, index=True)
    # Lifecycle / orchestration status
    lifecycle_status: Mapped[str] = mapped_column(String(32), default="DRAFT", nullable=False, index=True)
    connection_status: Mapped[str] = mapped_column(String(24), default="UNKNOWN", nullable=False)
    configuration_status: Mapped[str] = mapped_column(String(24), default="NONE", nullable=False)
    registration_status: Mapped[str] = mapped_column(String(32), default="NOT_REQUIRED", nullable=False)
    last_connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_discovery_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_configuration_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_auth_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_accounting_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_coa_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    health: Mapped[str] = mapped_column(String(24), default="unknown", nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    capabilities: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

class RadiusServer(Base, Timestamped):
    __tablename__ = "radius_servers"
    __table_args__ = (UniqueConstraint("host", "auth_port", name="uq_radius_server_host_port"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    group_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("radius_server_groups.id"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    environment: Mapped[str] = mapped_column(String(32), default="production", nullable=False)
    region: Mapped[str | None] = mapped_column(String(64), nullable=True)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    auth_port: Mapped[int] = mapped_column(Integer, default=1812, nullable=False)
    accounting_port: Mapped[int] = mapped_column(Integer, default=1813, nullable=False)
    coa_port: Mapped[int] = mapped_column(Integer, default=3799, nullable=False)
    api_key_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    draining: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    weight: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    version_metadata: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_request_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    health: Mapped[str] = mapped_column(String(24), default="unknown", nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

class AccountingEvent(Base):
    __tablename__ = "aaa_accounting_events"
    __table_args__ = (UniqueConstraint("tenant_id", "idempotency_key", name="uq_accounting_idempotency"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("aaa_tenants.id"), index=True, nullable=False)
    nas_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("aaa_nas.id"), index=True, nullable=False)
    subscriber_id: Mapped[uuid.UUID | None] = mapped_column(index=True, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    session_id: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(24), nullable=False)
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    input_octets: Mapped[int] = mapped_column(nullable=False, default=0)
    output_octets: Mapped[int] = mapped_column(nullable=False, default=0)
    raw_redacted: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

class ActiveSession(Base, Timestamped):
    __tablename__ = "aaa_sessions"
    __table_args__ = (UniqueConstraint("tenant_id", "session_id", name="uq_session_tenant_session"), Index("ix_session_tenant_status", "tenant_id", "status"))
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("aaa_tenants.id"), index=True, nullable=False)
    nas_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("aaa_nas.id"), index=True, nullable=False)
    subscriber_id: Mapped[uuid.UUID | None] = mapped_column(index=True, nullable=True)
    username: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    session_id: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="STARTING", nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_interim_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    input_octets: Mapped[int] = mapped_column(nullable=False, default=0)
    output_octets: Mapped[int] = mapped_column(nullable=False, default=0)
    session_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    framed_ip: Mapped[str | None] = mapped_column(String(45), nullable=True, index=True)
    mac_address: Mapped[str | None] = mapped_column(String(17), nullable=True, index=True)
    policy_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    termination_cause: Mapped[str | None] = mapped_column(String(128), nullable=True)

class OutboxEvent(Base):
    __tablename__ = "aaa_outbox"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    event_type: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(index=True, nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

class ConsumerInbox(Base):
    __tablename__ = "aaa_consumer_inbox"
    event_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    consumer: Mapped[str] = mapped_column(String(128), primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

class UsageProjection(Base, Timestamped):
    __tablename__ = "aaa_usage"
    __table_args__ = (UniqueConstraint("tenant_id", "subscriber_id", "period", name="uq_usage_subscriber_period"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("aaa_tenants.id"), index=True, nullable=False)
    subscriber_id: Mapped[uuid.UUID] = mapped_column(index=True, nullable=False)
    period: Mapped[str] = mapped_column(String(32), nullable=False)
    input_octets: Mapped[int] = mapped_column(nullable=False, default=0)
    output_octets: Mapped[int] = mapped_column(nullable=False, default=0)
    quota_bytes: Mapped[int | None] = mapped_column(nullable=True)
    fup_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

class RadiusCommand(Base, Timestamped):
    __tablename__ = "aaa_radius_commands"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("aaa_tenants.id"), index=True, nullable=False)
    nas_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("aaa_nas.id"), index=True, nullable=False)
    session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("aaa_sessions.id"), nullable=True, index=True)
    subscriber_id: Mapped[uuid.UUID | None] = mapped_column(index=True, nullable=True)
    command_type: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="PENDING", nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    attributes: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    result: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

class IpPool(Base, Timestamped):
    __tablename__ = "aaa_ip_pools"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_ip_pool_tenant_name"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("aaa_tenants.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    address_family: Mapped[str] = mapped_column(String(8), default="ipv4", nullable=False)
    cidr: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    nas_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("aaa_nas.id"), nullable=True, index=True)
    excluded: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

class IpLease(Base, Timestamped):
    __tablename__ = "aaa_ip_leases"
    __table_args__ = (UniqueConstraint("tenant_id", "address", name="uq_lease_tenant_address"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("aaa_tenants.id"), index=True, nullable=False)
    pool_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("aaa_ip_pools.id"), index=True, nullable=False)
    subscriber_id: Mapped[uuid.UUID | None] = mapped_column(index=True, nullable=True)
    address: Mapped[str] = mapped_column(String(64), nullable=False)
    reservation: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    active_session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("aaa_sessions.id"), nullable=True, index=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

class AuditLog(Base):
    __tablename__ = "aaa_audit_log"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(index=True, nullable=True)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    target_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[str] = mapped_column(String(64), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    detail: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

class NasCredential(Base, Timestamped):
    __tablename__ = "nas_credentials"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    nas_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("aaa_nas.id"), index=True, nullable=False)
    username_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    secret_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    credential_type: Mapped[str] = mapped_column(String(32), default="password", nullable=False)
    key_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    api_port: Mapped[int] = mapped_column(Integer, default=8729, nullable=False)
    tls_settings: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    certificate_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False)
    last_rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

class NasRadiusAssignment(Base, Timestamped):
    """Relationship between a NAS and a logical RADIUS server.

    The shared secret is assignment-specific because primary and secondary
    servers may use different secrets. Only the encrypted form is persisted.
    """
    __tablename__ = "nas_radius_assignments"
    __table_args__ = (UniqueConstraint("nas_id", "radius_server_id", name="uq_nas_radius_assignment"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    nas_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("aaa_nas.id"), index=True, nullable=False)
    radius_server_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("radius_servers.id"), index=True, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    role: Mapped[str] = mapped_column(String(16), default="secondary", nullable=False)
    services: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    auth_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    accounting_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    coa_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=3000, nullable=False)
    source_address: Mapped[str | None] = mapped_column(String(64))
    secret_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    secret_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    desired_status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    applied_status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    registration_status: Mapped[str] = mapped_column(String(32), default="PENDING", nullable=False)
    remote_object_id: Mapped[str | None] = mapped_column(String(128))
    manual_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_synchronized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_reason: Mapped[str | None] = mapped_column(String(500))
    radius_server: Mapped["RadiusServer | None"] = relationship(foreign_keys=[radius_server_id], lazy="joined")

class NasCapability(Base, Timestamped):
    """Detected RouterOS capabilities for a NAS. Flags are stored normalized."""
    __tablename__ = "nas_capabilities"
    __table_args__ = (UniqueConstraint("nas_id", "version", name="uq_nas_capability_version"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    nas_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("aaa_nas.id"), index=True, nullable=False)
    version: Mapped[str] = mapped_column(String(32), default="0", nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    flags: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    raw: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

class NasHealthCheck(Base):
    """Append-only health check result. Diagnostics are sanitized before store."""
    __tablename__ = "nas_health_checks"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    nas_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("aaa_nas.id"), index=True, nullable=False)
    check_type: Mapped[str] = mapped_column(String(48), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(nullable=True)
    diagnostic: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

class NasRemoteObject(Base, Timestamped):
    """Backend-observed RouterOS objects. Ownership decides reconcile behaviour."""
    __tablename__ = "nas_remote_objects"
    __table_args__ = (UniqueConstraint("nas_id", "object_type", "remote_object_id", name="uq_nas_remote_object"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    nas_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("aaa_nas.id"), index=True, nullable=False)
    object_type: Mapped[str] = mapped_column(String(48), nullable=False)
    remote_object_id: Mapped[str] = mapped_column(String(128), nullable=False)
    backend_assignment_id: Mapped[uuid.UUID | None] = mapped_column(index=True, nullable=True)
    last_observed_attributes: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    last_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ownership: Mapped[str] = mapped_column(String(24), default="UNKNOWN", nullable=False)

class NasSecretRotation(Base, Timestamped):
    """Staged shared-secret rotation between the manually hosted FreeRADIUS and
    the router. Old and new secrets are stored encrypted; never returned.
    """
    __tablename__ = "nas_secret_rotations"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    nas_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("aaa_nas.id"), index=True, nullable=False)
    assignment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("nas_radius_assignments.id"), index=True, nullable=False)
    state: Mapped[str] = mapped_column(String(40), default="ROTATION_DRAFT", nullable=False)
    old_secret_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    old_secret_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    new_secret_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    new_secret_version: Mapped[int] = mapped_column(Integer, nullable=False)
    freeradius_confirmations: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_by: Mapped[str] = mapped_column(String(128), default="internal-radius", nullable=False)
    rollback_available_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

class NasSnapshot(Base):
    __tablename__ = "nas_configuration_snapshots"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    nas_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("aaa_nas.id"), index=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    scope: Mapped[str] = mapped_column(String(64), default="radius_aaa", nullable=False)
    source: Mapped[str] = mapped_column(String(24), nullable=False)
    sanitized_configuration: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    configuration_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

class NasJob(Base, Timestamped):
    __tablename__ = "nas_configuration_jobs"
    __table_args__ = (UniqueConstraint("nas_id", "idempotency_key", name="uq_nas_job_idempotency"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    nas_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("aaa_nas.id"), index=True, nullable=False)
    job_type: Mapped[str] = mapped_column(String(48), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="PENDING", nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    maximum_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    safe_result: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

class NasDesiredConfiguration(Base, Timestamped):
    """Versioned, secret-free desired state for the AAA-managed router scope."""
    __tablename__ = "nas_desired_configurations"
    __table_args__ = (UniqueConstraint("nas_id", "version", name="uq_nas_desired_configuration_version"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    nas_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("aaa_nas.id"), index=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    configuration: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False)
    created_by: Mapped[str] = mapped_column(String(128), default="internal-radius", nullable=False)

class NasChangePlan(Base, Timestamped):
    """An immutable preview. Applying always re-checks expiry and desired version."""
    __tablename__ = "nas_change_plans"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    nas_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("aaa_nas.id"), index=True, nullable=False)
    desired_configuration_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("nas_desired_configurations.id"), nullable=False)
    current_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("nas_configuration_snapshots.id"), nullable=True)
    planned_changes: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    risk: Mapped[str] = mapped_column(String(24), default="low", nullable=False)
    validation: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="DRAFT", nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

class NasSecretReveal(Base):
    __tablename__ = "nas_secret_reveals"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    assignment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("nas_radius_assignments.id"), index=True, nullable=False)
    rotation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("nas_secret_rotations.id"), nullable=True, index=True)
    secret_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

class NasOperationLock(Base):
    """Database-level per-NAS lock used when Redis is unavailable. Never the
    permanent source of truth; Redis remains the primary lock store."""
    __tablename__ = "nas_operation_locks"
    nas_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    owner: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


# ===========================================================================
# Milestone 3 — Advanced Network Control
# ===========================================================================

class NetworkPolicy(Base, Timestamped):
    """A vendor-neutral network policy aggregate with versioned bodies."""
    __tablename__ = "nc_policies"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_nc_policy_code"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("aaa_tenants.id"), index=True, nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    state: Mapped[str] = mapped_column(String(32), default="DRAFT", nullable=False, index=True)
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("nc_policy_versions.id"), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class NetworkPolicyVersion(Base, Timestamped):
    """Immutable published policy body. Editing creates a new version."""
    __tablename__ = "nc_policy_versions"
    __table_args__ = (UniqueConstraint("policy_id", "version", name="uq_nc_policy_version"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("aaa_tenants.id"), index=True, nullable=False)
    policy_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("nc_policies.id"), index=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(32), default="DRAFT", nullable=False, index=True)
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Vendor-neutral policy body (bandwidth, bursts, priority, ip policy, pools,
    # timeouts, filter, fup, qos references). Never RouterOS command strings.
    body: Mapped[dict] = mapped_column(JSON, nullable=False)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class BandwidthProfile(Base, Timestamped):
    __tablename__ = "nc_bandwidth_profiles"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_nc_bw_code"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("aaa_tenants.id"), index=True, nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    upload_kbps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    download_kbps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    upload_min_kbps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    download_min_kbps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    burst_upload_kbps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    burst_download_kbps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    burst_threshold_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    burst_duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    priority: Mapped[str] = mapped_column(String(16), default="normal", nullable=False)
    queue_type: Mapped[str] = mapped_column(String(32), default="default", nullable=False)


class TrafficClass(Base, Timestamped):
    __tablename__ = "nc_traffic_classes"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_nc_tc_code"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("aaa_tenants.id"), index=True, nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    dscp: Mapped[str | None] = mapped_column(String(32), nullable=True)
    protocol: Mapped[str | None] = mapped_column(String(16), nullable=True)
    src_group: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dst_group: Mapped[str | None] = mapped_column(String(64), nullable=True)
    src_port: Mapped[str | None] = mapped_column(String(32), nullable=True)
    dst_port: Mapped[str | None] = mapped_column(String(32), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cir_kbps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mir_kbps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    packet_mark: Mapped[str | None] = mapped_column(String(64), nullable=True)
    connection_mark: Mapped[str | None] = mapped_column(String(64), nullable=True)
    queue_discipline: Mapped[str] = mapped_column(String(32), default="default", nullable=False)
    congestion_action: Mapped[str] = mapped_column(String(32), default="drop", nullable=False)


class QosProfile(Base, Timestamped):
    __tablename__ = "nc_qos_profiles"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_nc_qos_code"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("aaa_tenants.id"), index=True, nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    tier: Mapped[str] = mapped_column(String(32), default="standard", nullable=False)
    traffic_class_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    params: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class FairUsagePolicy(Base, Timestamped):
    """FUP as a first-class policy: thresholds, progressive tiers, reset rule."""
    __tablename__ = "nc_fup_policies"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_nc_fup_code"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("aaa_tenants.id"), index=True, nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    cycle: Mapped[str] = mapped_column(String(16), default="monthly", nullable=False)
    # thresholds: [{"label": "...", "limit_bytes": int, "upload_kbps": int|None,
    #              "download_kbps": int|None, "combined": bool}]
    thresholds: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    reset_rule: Mapped[str] = mapped_column(String(32), default="cycle_start", nullable=False)
    grace_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)


class FupCounter(Base, Timestamped):
    __tablename__ = "nc_fup_counters"
    __table_args__ = (UniqueConstraint("tenant_id", "subscriber_id", "cycle", name="uq_nc_fup_counter"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("aaa_tenants.id"), index=True, nullable=False)
    subscriber_id: Mapped[uuid.UUID] = mapped_column(index=True, nullable=False)
    cycle: Mapped[str] = mapped_column(String(32), nullable=False)
    input_octets: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_octets: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    topup_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    active_tier: Mapped[str | None] = mapped_column(String(64), nullable=True)
    throttled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SubscriberPolicyAssignment(Base, Timestamped):
    __tablename__ = "nc_subscriber_policy_assignments"
    __table_args__ = (UniqueConstraint("tenant_id", "subscriber_id", "source", name="uq_nc_assignment"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("aaa_tenants.id"), index=True, nullable=False)
    subscriber_id: Mapped[uuid.UUID] = mapped_column(index=True, nullable=False)
    policy_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("nc_policy_versions.id"), index=True, nullable=False)
    source: Mapped[str] = mapped_column(String(32), default="subscriber", nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class PolicyOverride(Base, Timestamped):
    __tablename__ = "nc_policy_overrides"
    __table_args__ = (Index("ix_nc_override_active", "tenant_id", "subscriber_id", "active"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("aaa_tenants.id"), index=True, nullable=False)
    subscriber_id: Mapped[uuid.UUID] = mapped_column(index=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), default="temporary", nullable=False)
    body: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class PolicyDecision(Base, Timestamped):
    """Every policy evaluation is persisted and explainable."""
    __tablename__ = "nc_policy_decisions"
    __table_args__ = (Index("ix_nc_decision_subscriber", "tenant_id", "subscriber_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("aaa_tenants.id"), index=True, nullable=False)
    subscriber_id: Mapped[uuid.UUID | None] = mapped_column(index=True, nullable=True)
    customer_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    subscription_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    session_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    nas_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("aaa_nas.id"), nullable=True, index=True)
    policy_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("nc_policy_versions.id"), nullable=True)
    input_facts: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    fact_versions: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    rules_evaluated: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    winning_rules: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    rejected_rules: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    resulting_policy: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    explanation: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    actor: Mapped[str | None] = mapped_column(String(128), nullable=True)


class EnforcementAction(Base, Timestamped):
    __tablename__ = "nc_enforcement_actions"
    __table_args__ = (UniqueConstraint("tenant_id", "idempotency_key", name="uq_nc_enforcement_idem"), Index("ix_nc_enforcement_status", "tenant_id", "status"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("aaa_tenants.id"), index=True, nullable=False)
    action_type: Mapped[str] = mapped_column(String(32), nullable=False)
    trigger: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    subscriber_id: Mapped[uuid.UUID | None] = mapped_column(index=True, nullable=True)
    nas_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("aaa_nas.id"), nullable=True, index=True)
    policy_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("nc_policy_versions.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="PENDING", nullable=False)
    target: Mapped[str | None] = mapped_column(String(255), nullable=True)
    attributes: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor: Mapped[str | None] = mapped_column(String(128), nullable=True)
    result: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class EnforcementAttempt(Base):
    __tablename__ = "nc_enforcement_attempts"
    __table_args__ = (Index("ix_nc_attempt_action", "enforcement_action_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    enforcement_action_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("nc_enforcement_actions.id"), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detail: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class DevicePolicyBinding(Base, Timestamped):
    __tablename__ = "nc_device_policy_bindings"
    __table_args__ = (UniqueConstraint("tenant_id", "nas_id", "resource_key", name="uq_nc_binding"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("aaa_tenants.id"), index=True, nullable=False)
    nas_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("aaa_nas.id"), index=True, nullable=False)
    policy_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("nc_policy_versions.id"), index=True, nullable=False)
    resource_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_key: Mapped[str] = mapped_column(String(255), nullable=False)
    remote_object_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="PLANNED", nullable=False)


class PolicyDriftRecord(Base, Timestamped):
    __tablename__ = "nc_policy_drift"
    __table_args__ = (Index("ix_nc_drift_status", "tenant_id", "nas_id", "status"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("aaa_tenants.id"), index=True, nullable=False)
    nas_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("aaa_nas.id"), index=True, nullable=False)
    drift_type: Mapped[str] = mapped_column(String(64), nullable=False)
    classification: Mapped[str] = mapped_column(String(48), default="INFORMATIONAL", nullable=False)
    resource_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    resource_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    detail: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="OPEN", nullable=False)


class ControlAction(Base, Timestamped):
    """CoA/Disconnect registry with outcome (ACK/NAK/timeout) persistence."""
    __tablename__ = "nc_control_actions"
    __table_args__ = (UniqueConstraint("tenant_id", "idempotency_key", name="uq_nc_control_idem"), Index("ix_nc_control_status", "tenant_id", "status"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("aaa_tenants.id"), index=True, nullable=False)
    action_type: Mapped[str] = mapped_column(String(16), nullable=False)  # COA | DISCONNECT
    trigger: Mapped[str] = mapped_column(String(64), nullable=False)
    nas_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("aaa_nas.id"), index=True, nullable=False)
    session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("aaa_sessions.id"), nullable=True, index=True)
    subscriber_id: Mapped[uuid.UUID | None] = mapped_column(index=True, nullable=True)
    username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    session_identifier: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    requested_attributes: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="PENDING", nullable=False)
    strategy: Mapped[str] = mapped_column(String(32), default="COA", nullable=False)  # COA | DISCONNECT_AND_REAUTHORIZE
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ack_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    nak_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    timeout_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    actor: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)


class SessionTimeline(Base):
    __tablename__ = "nc_session_timeline"
    __table_args__ = (Index("ix_nc_timeline_session", "session_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("aaa_tenants.id"), index=True, nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("aaa_sessions.id"), index=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class RouterReadinessReport(Base, Timestamped):
    __tablename__ = "nc_router_readiness"
    __table_args__ = (Index("ix_nc_readiness_nas", "tenant_id", "nas_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("aaa_tenants.id"), index=True, nullable=False)
    nas_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("aaa_nas.id"), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    checks: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    winbox_guide: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

