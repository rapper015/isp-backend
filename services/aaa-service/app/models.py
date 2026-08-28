"""Persistent AAA state. PostgreSQL is authoritative; caches are never authoritative."""
import uuid
from datetime import datetime
from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
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
    __tablename__ = "aaa_nas"
    __table_args__ = (UniqueConstraint("tenant_id", "source_ip", name="uq_nas_tenant_source_ip"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("aaa_tenants.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    short_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_ip: Mapped[str] = mapped_column(String(45), nullable=False, index=True)
    source_cidr: Mapped[str | None] = mapped_column(String(50), nullable=True)
    nas_identifier: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    vendor: Mapped[str] = mapped_column(String(64), default="generic", nullable=False)
    device_type: Mapped[str] = mapped_column(String(64), default="router", nullable=False)
    auth_port: Mapped[int] = mapped_column(Integer, default=1812, nullable=False)
    accounting_port: Mapped[int] = mapped_column(Integer, default=1813, nullable=False)
    coa_port: Mapped[int] = mapped_column(Integer, default=3799, nullable=False)
    secret_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    secret_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    allowed_services: Mapped[list] = mapped_column(JSON, default=lambda: ["pppoe", "hotspot"], nullable=False)
    allowed_methods: Mapped[list] = mapped_column(JSON, default=lambda: ["pap"], nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    radius_group_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("radius_server_groups.id"), nullable=True, index=True)
    last_auth_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_accounting_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_coa_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    health: Mapped[str] = mapped_column(String(24), default="unknown", nullable=False)
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
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

class NasRadiusAssignment(Base, Timestamped):
    __tablename__ = "nas_radius_assignments"
    __table_args__ = (UniqueConstraint("nas_id", "radius_server_id", name="uq_nas_radius_assignment"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    nas_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("aaa_nas.id"), index=True, nullable=False)
    radius_server_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("radius_servers.id"), index=True, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    role: Mapped[str] = mapped_column(String(16), default="secondary", nullable=False)
    services: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    source_address: Mapped[str | None] = mapped_column(String(64))
    secret_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    secret_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    desired_status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    applied_status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    registration_status: Mapped[str] = mapped_column(String(32), default="PENDING", nullable=False)
    remote_object_id: Mapped[str | None] = mapped_column(String(128))
    manual_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_reason: Mapped[str | None] = mapped_column(String(500))

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
