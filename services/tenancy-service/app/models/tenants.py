"""Tenant registry, lifecycle, provisioning, configuration, domains, branding,
feature flags, entitlements, quotas and secrets (control-plane data)."""
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, Timestamped, UuidPk


class Tenant(Base, Timestamped, UuidPk):
    __tablename__ = "ten_tenants"
    __table_args__ = (Index("ix_ten_tenant_status", "status"),)

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    code: Mapped[str] = mapped_column(String(40), nullable=False, unique=True, index=True)
    legal_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    country: Mapped[str | None] = mapped_column(String(4), nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="INR", nullable=False)
    timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    locale: Mapped[str | None] = mapped_column(String(16), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="REQUESTED", nullable=False)
    isolation_mode: Mapped[str] = mapped_column(String(32), default="SHARED_SCHEMA_WITH_RLS", nullable=False)
    plan_ref: Mapped[str | None] = mapped_column(String(80), nullable=True)
    provision_state: Mapped[str | None] = mapped_column(String(40), nullable=True)
    feature_flags_ref: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    suspension_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class TenantDatabase(Base, Timestamped, UuidPk):
    __tablename__ = "ten_tenant_databases"
    __table_args__ = (UniqueConstraint("tenant_id", "alias", name="uq_ten_tenant_db_alias"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    alias: Mapped[str] = mapped_column(String(80), nullable=False)
    isolation_mode: Mapped[str] = mapped_column(String(32), default="SHARED_SCHEMA_WITH_RLS", nullable=False)
    host: Mapped[str | None] = mapped_column(String(160), nullable=True)
    db_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    secret_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)  # encrypted credential reference
    state: Mapped[str] = mapped_column(String(24), default="PENDING", nullable=False)
    pool_config: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    last_health_check: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    health_state: Mapped[str] = mapped_column(String(24), default="UNKNOWN", nullable=False)


class TenantDomain(Base, Timestamped, UuidPk):
    __tablename__ = "ten_tenant_domains"
    __table_args__ = (
        UniqueConstraint("domain", name="uq_ten_domain"),
        Index("ix_ten_domain_tenant", "tenant_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    verification_token: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tls_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="PENDING", nullable=False)
    changed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class TenantConfiguration(Base, Timestamped, UuidPk):
    __tablename__ = "ten_tenant_configs"
    __table_args__ = (UniqueConstraint("tenant_id", "category", name="uq_ten_tenant_config_category"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(48), nullable=False)  # legal|locale|tax|invoice|portal|email|sms|...
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    config: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    changed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class TenantConfigurationVersion(Base, Timestamped, UuidPk):
    __tablename__ = "ten_tenant_config_versions"
    __table_args__ = (UniqueConstraint("config_id", "version", name="uq_ten_config_version"),)

    config_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(48), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    config: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    changed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class TenantSecret(Base, Timestamped, UuidPk):
    """Encrypted secret references (integration credentials, branding keys)."""

    __tablename__ = "ten_tenant_secrets"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_ten_tenant_secret"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    secret_ref: Mapped[str] = mapped_column(String(255), nullable=False)  # encrypted
    category: Mapped[str] = mapped_column(String(48), default="INTEGRATION", nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class FeatureFlag(Base, Timestamped, UuidPk):
    __tablename__ = "ten_feature_flags"
    __table_args__ = (UniqueConstraint("code", name="uq_ten_feature_code"),)

    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    platform_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    environment: Mapped[str] = mapped_column(String(32), default="ALL", nullable=False)
    state: Mapped[str] = mapped_column(String(24), default="ENABLED", nullable=False)


class TenantFeature(Base, Timestamped, UuidPk):
    __tablename__ = "ten_tenant_features"
    __table_args__ = (UniqueConstraint("tenant_id", "flag_id", name="uq_ten_tenant_feature"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    flag_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    changed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class Entitlement(Base, Timestamped, UuidPk):
    __tablename__ = "ten_entitlements"
    __table_args__ = (UniqueConstraint("code", name="uq_ten_entitlement_code"),)

    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), default="LICENSE", nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class TenantEntitlement(Base, Timestamped, UuidPk):
    __tablename__ = "ten_tenant_entitlements"
    __table_args__ = (UniqueConstraint("tenant_id", "entitlement_id", name="uq_ten_tenant_entitlement"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    entitlement_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    granted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    granted_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class Quota(Base, Timestamped, UuidPk):
    __tablename__ = "ten_quotas"
    __table_args__ = (UniqueConstraint("kind", name="uq_ten_quota_kind"),)

    kind: Mapped[str] = mapped_column(String(40), nullable=False)  # from QUOTA_KINDS
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_limit: Mapped[float | None] = mapped_column(Float, nullable=True)


class TenantQuota(Base, Timestamped, UuidPk):
    __tablename__ = "ten_tenant_quotas"
    __table_args__ = (UniqueConstraint("tenant_id", "quota_id", name="uq_ten_tenant_quota"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    quota_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    limit: Mapped[float | None] = mapped_column(Float, nullable=True)
    used: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    changed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class TenantHealth(Base, Timestamped, UuidPk):
    __tablename__ = "ten_tenant_health"
    __table_args__ = (Index("ix_ten_health_tenant", "tenant_id"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    check_type: Mapped[str] = mapped_column(String(48), nullable=False)
    result: Mapped[str] = mapped_column(String(24), default="UNKNOWN", nullable=False)
    detail: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
