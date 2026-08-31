"""Platform identity, tenant membership, scoped RBAC, separation of duty,
approvals (maker-checker), service accounts, API credentials, impersonation."""
import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, Timestamped, UuidPk


class UserIdentity(Base, Timestamped, UuidPk):
    """One platform identity; membership in tenants is separate."""

    __tablename__ = "ten_user_identities"
    __table_args__ = (UniqueConstraint("username", name="uq_ten_user_username"),)

    username: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    email: Mapped[str | None] = mapped_column(String(160), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="ACTIVE", nullable=False)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    token_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class TenantMembership(Base, Timestamped, UuidPk):
    __tablename__ = "ten_memberships"
    __table_args__ = (
        UniqueConstraint("user_id", "tenant_id", name="uq_ten_membership_user_tenant"),
        Index("ix_ten_membership_tenant", "tenant_id"),
    )

    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(24), default="PENDING", nullable=False)
    joined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    granted_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class MembershipRole(Base, Timestamped, UuidPk):
    """Scoped role assignment: membership x role x org-unit x scope."""

    __tablename__ = "ten_membership_roles"
    __table_args__ = (Index("ix_ten_membership_role", "membership_id", "role_id"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    membership_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    role_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    org_unit_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    scope_kind: Mapped[str] = mapped_column(String(24), default="TENANT", nullable=False)
    inheritance: Mapped[str] = mapped_column(String(24), default="EXPLICIT", nullable=False)
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    assigned_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class OrganizationMembership(Base, Timestamped, UuidPk):
    __tablename__ = "ten_org_memberships"
    __table_args__ = (UniqueConstraint("tenant_id", "org_unit_id", "user_id", name="uq_ten_org_membership"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    org_unit_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="ACTIVE", nullable=False)


class Permission(Base, Timestamped, UuidPk):
    """Global permission registry (action-level, e.g. customers.create)."""

    __tablename__ = "ten_permissions"
    __table_args__ = (UniqueConstraint("code", name="uq_ten_permission_code"),)

    code: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(String(48), default="general", nullable=False)


class RoleTemplate(Base, Timestamped, UuidPk):
    __tablename__ = "ten_role_templates"
    __table_args__ = (UniqueConstraint("code", name="uq_ten_role_template_code"),)

    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    permission_codes: Mapped[list] = mapped_column(JSON, default=list, nullable=False)


class Role(Base, Timestamped, UuidPk):
    __tablename__ = "ten_roles"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_ten_role_code"),)

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)  # None = platform default role
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class RolePermission(Base, Timestamped, UuidPk):
    __tablename__ = "ten_role_permissions"
    __table_args__ = (UniqueConstraint("role_id", "permission_code", name="uq_ten_role_permission"),)

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    role_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    permission_code: Mapped[str] = mapped_column(String(120), nullable=False)
    deny: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class SodConstraint(Base, Timestamped, UuidPk):
    __tablename__ = "ten_sod_constraints"
    __table_args__ = (UniqueConstraint("operation", "maker_permission", "checker_permission", name="uq_ten_sod"),)

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    operation: Mapped[str] = mapped_column(String(80), nullable=False)
    maker_permission: Mapped[str] = mapped_column(String(120), nullable=False)
    checker_permission: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Approval(Base, Timestamped, UuidPk):
    """Maker-checker approval record (financial + tenant-wide + access changes)."""

    __tablename__ = "ten_approvals"
    __table_args__ = (Index("ix_ten_approval_tenant", "tenant_id"),)

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    operation: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    state: Mapped[str] = mapped_column(String(24), default="PENDING", nullable=False)
    requested_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    approval_level: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    detail: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ServiceAccount(Base, Timestamped, UuidPk):
    __tablename__ = "ten_service_accounts"
    __table_args__ = (UniqueConstraint("tenant_id", "service", "name", name="uq_ten_service_account"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    service: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    permission_codes: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="ACTIVE", nullable=False)
    ip_restrictions: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class ApiCredential(Base, Timestamped, UuidPk):
    __tablename__ = "ten_api_credentials"
    __table_args__ = (Index("ix_ten_api_credential_tenant", "tenant_id"),)

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    service_account_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    secret_ref: Mapped[str] = mapped_column(String(255), nullable=False)  # encrypted
    status: Mapped[str] = mapped_column(String(24), default="ACTIVE", nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ImpersonationSession(Base, Timestamped, UuidPk):
    __tablename__ = "ten_impersonation_sessions"

    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    admin_user: Mapped[str] = mapped_column(String(128), nullable=False)
    target_user: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str] = mapped_column(String(24), default="REQUESTED", nullable=False)
    read_only: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    ticket_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
