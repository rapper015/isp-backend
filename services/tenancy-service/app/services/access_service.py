"""Scoped RBAC: memberships, roles, role permissions, scoped assignments,
separation-of-duty approvals (maker-checker), service accounts, API credentials
and platform-administrator impersonation sessions."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.access import check_sod, permissions_for, validate_approval_transition
from ..domain.exceptions import (
    ImpersonationError,
    NotFoundError,
    PermissionDeniedError,
    SeparationOfDutyError,
    ValidationError,
)
from ..domain.secrets import encrypt_secret
from ..events import outbox
from ..models import (
    ApiCredential,
    Approval,
    ImpersonationSession,
    MembershipRole,
    OrganizationMembership,
    Role,
    RolePermission,
    ServiceAccount,
    SodConstraint,
    TenantMembership,
)
from .audit_service import audit, correlation


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_role_or_404(session: Session, tenant_id, role_id) -> Role:
    role = session.get(Role, role_id)
    if role is None or (role.tenant_id not in (None, tenant_id)):
        raise NotFoundError("role not found")
    return role


# ---------------------------------------------------------------------------
# Memberships
# ---------------------------------------------------------------------------
def create_membership(session: Session, tenant_id, *, user_id: str,
                      granted_by: str = "system") -> TenantMembership:
    existing = session.scalars(select(TenantMembership).where(
        TenantMembership.user_id == user_id, TenantMembership.tenant_id == tenant_id)).first()
    if existing is not None:
        existing.status = "ACTIVE"
        return existing
    row = TenantMembership(user_id=user_id, tenant_id=tenant_id, status="ACTIVE",
                           joined_at=_now(), granted_by=granted_by)
    session.add(row)
    session.flush()
    audit(session, tenant_id, granted_by, "membership.created", resource_type="membership",
          resource_id=row.id, after={"user": user_id}, correlation_id=correlation(None))
    outbox(session, "tenancy.membership.changed.v1", tenant_id, correlation(None),
           {"tenant_id": str(tenant_id), "user_id": user_id, "action": "created"})
    return row


def revoke_membership(session: Session, tenant_id, membership_id: uuid.UUID, *, actor: str = "system") -> None:
    row = session.get(TenantMembership, membership_id)
    if row is None or row.tenant_id != tenant_id:
        raise NotFoundError("membership not found")
    row.status = "REVOKED"
    session.flush()
    audit(session, tenant_id, actor, "membership.revoked", resource_type="membership",
          resource_id=row.id, after={"user": row.user_id})


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------
def create_role(session: Session, tenant_id, *, code: str, name: str, actor: str = "system") -> Role:
    existing = session.scalars(select(Role).where(Role.tenant_id == tenant_id, Role.code == code)).first()
    if existing is not None:
        from ..domain.exceptions import DuplicateError
        raise DuplicateError(f"role {code!r} already exists")
    role = Role(tenant_id=tenant_id, code=code, name=name, created_by=actor)
    session.add(role)
    session.flush()
    audit(session, tenant_id, actor, "role.created", resource_type="role", resource_id=role.id,
          after={"code": code})
    outbox(session, "tenancy.role.changed.v1", tenant_id, correlation(None),
           {"tenant_id": str(tenant_id), "role": code, "action": "created"})
    return role


def set_role_permissions(session: Session, tenant_id, role_id: uuid.UUID, *, permission_codes: list[str],
                         actor: str = "system") -> Role:
    role = get_role_or_404(session, tenant_id, role_id)
    for row in session.scalars(select(RolePermission).where(RolePermission.role_id == role.id)):
        session.delete(row)
    for code in permission_codes:
        session.add(RolePermission(tenant_id=tenant_id, role_id=role.id, permission_code=code))
    session.flush()
    audit(session, tenant_id, actor, "role.permissions.updated", resource_type="role",
          resource_id=role.id, after={"permissions": permission_codes})
    return role


def effective_permissions(session: Session, tenant_id, user_id: str) -> set[str]:
    """Resolve the permission set for a user across all active memberships/roles."""
    permissions: set[str] = set()
    memberships = list(session.scalars(select(TenantMembership).where(
        TenantMembership.user_id == user_id, TenantMembership.tenant_id == tenant_id,
        TenantMembership.status == "ACTIVE")))
    for membership in memberships:
        for assignment in session.scalars(select(MembershipRole).where(
                MembershipRole.membership_id == membership.id, MembershipRole.is_active.is_(True))):
            role = session.get(Role, assignment.role_id)
            if role is None:
                continue
            for rp in session.scalars(select(RolePermission).where(RolePermission.role_id == role.id)):
                permissions.add(rp.permission_code)
    return permissions


def assign_role(session: Session, tenant_id, *, membership_id: uuid.UUID, role_id: uuid.UUID,
                org_unit_id: uuid.UUID | None = None, scope_kind: str = "TENANT",
                assigned_by: str = "system") -> MembershipRole:
    membership = session.get(TenantMembership, membership_id)
    if membership is None or membership.tenant_id != tenant_id:
        raise NotFoundError("membership not found")
    get_role_or_404(session, tenant_id, role_id)
    assignment = MembershipRole(tenant_id=tenant_id, membership_id=membership.id, role_id=role_id,
                                org_unit_id=org_unit_id, scope_kind=scope_kind, assigned_by=assigned_by)
    session.add(assignment)
    session.flush()
    audit(session, tenant_id, assigned_by, "role.assigned", resource_type="membership_role",
          resource_id=assignment.id, after={"membership": str(membership.id), "role": str(role_id),
                                            "scope": scope_kind, "org_unit": str(org_unit_id)})
    return assignment


# ---------------------------------------------------------------------------
# Separation of duty + approvals
# ---------------------------------------------------------------------------
def request_approval(session: Session, tenant_id, *, operation: str, requested_by: str,
                     reason: str | None = None, detail: dict | None = None,
                     resource_type: str | None = None, resource_id: str | None = None,
                     correlation_id: str | None = None) -> Approval:
    approval = Approval(tenant_id=tenant_id, operation=operation, requested_by=requested_by,
                        reason=reason, detail=detail or {}, resource_type=resource_type,
                        resource_id=resource_id, correlation_id=correlation_id)
    session.add(approval)
    session.flush()
    audit(session, tenant_id, requested_by, "approval.requested", resource_type="approval",
          resource_id=approval.id, after={"operation": operation})
    return approval


def decide_approval(session: Session, tenant_id, approval_id: uuid.UUID, *, decision: str,
                    decided_by: str, reason: str | None = None) -> Approval:
    approval = session.get(Approval, approval_id)
    if approval is None or approval.tenant_id != tenant_id:
        raise NotFoundError("approval not found")
    if not validate_approval_transition(approval.state, decision):
        raise ValidationError(f"cannot transition approval from {approval.state} to {decision}")
    if decision == "APPROVED":
        check_sod(approval.requested_by or "", decided_by, operation=approval.operation,
                  constraints=list(session.scalars(select(SodConstraint).where(
                      SodConstraint.operation == approval.operation, SodConstraint.is_active.is_(True)))))
    approval.state = decision
    approval.approved_by = decided_by
    approval.decided_at = _now()
    session.flush()
    audit(session, tenant_id, decided_by, "approval.decided", resource_type="approval",
          resource_id=approval.id, after={"decision": decision}, reason=reason or approval.reason)
    return approval


# ---------------------------------------------------------------------------
# Service accounts + API credentials
# ---------------------------------------------------------------------------
def create_service_account(session: Session, tenant_id, *, service: str, name: str,
                           permission_codes: list[str], ip_restrictions: list[str] | None = None,
                           created_by: str = "system") -> ServiceAccount:
    existing = session.scalars(select(ServiceAccount).where(
        ServiceAccount.tenant_id == tenant_id, ServiceAccount.service == service,
        ServiceAccount.name == name)).first()
    if existing is not None:
        from ..domain.exceptions import DuplicateError
        raise DuplicateError("service account already exists")
    account = ServiceAccount(tenant_id=tenant_id, service=service, name=name,
                             permission_codes=permission_codes, ip_restrictions=ip_restrictions or [],
                             created_by=created_by)
    session.add(account)
    session.flush()
    audit(session, tenant_id, created_by, "service_account.created", resource_type="service_account",
          resource_id=account.id, after={"service": service, "name": name})
    return account


def issue_api_credential(session: Session, tenant_id, *, service_account_id: uuid.UUID, name: str,
                         expires_in_days: int = 90, actor: str = "system") -> ApiCredential:
    account = session.get(ServiceAccount, service_account_id)
    if account is None or account.tenant_id != tenant_id:
        raise NotFoundError("service account not found")
    plain = uuid.uuid4().hex + uuid.uuid4().hex
    credential = ApiCredential(tenant_id=tenant_id, service_account_id=account.id, name=name,
                               secret_ref=encrypt_secret(plain),
                               expires_at=_now() + timedelta(days=expires_in_days))
    session.add(credential)
    session.flush()
    audit(session, tenant_id, actor, "api_credential.issued", resource_type="api_credential",
          resource_id=credential.id, after={"name": name, "expires_in_days": expires_in_days})
    # The raw secret is returned exactly once at issuance.
    return credential


def rotate_api_credential(session: Session, tenant_id, credential_id: uuid.UUID, *, actor: str = "system") -> ApiCredential:
    credential = session.get(ApiCredential, credential_id)
    if credential is None or credential.tenant_id != tenant_id:
        raise NotFoundError("api credential not found")
    credential.secret_ref = encrypt_secret(uuid.uuid4().hex + uuid.uuid4().hex)
    credential.status = "ACTIVE"
    credential.expires_at = _now() + timedelta(days=90)
    session.flush()
    audit(session, tenant_id, actor, "api_credential.rotated", resource_type="api_credential",
          resource_id=credential.id, after={"status": "ROTATING"})
    return credential


def revoke_api_credential(session: Session, tenant_id, credential_id: uuid.UUID, *, actor: str = "system") -> None:
    credential = session.get(ApiCredential, credential_id)
    if credential is None or credential.tenant_id != tenant_id:
        raise NotFoundError("api credential not found")
    credential.status = "REVOKED"
    session.flush()
    audit(session, tenant_id, actor, "api_credential.revoked", resource_type="api_credential",
          resource_id=credential.id)


# ---------------------------------------------------------------------------
# Platform-admin impersonation
# ---------------------------------------------------------------------------
def start_impersonation(session: Session, tenant_id, *, admin_user: str, target_user: str,
                        reason: str, ticket_ref: str | None = None, read_only: bool = True,
                        ttl_minutes: int = 30, correlation_id: str | None = None) -> ImpersonationSession:
    request_id = correlation(correlation_id)
    if not reason or not reason.strip():
        raise ImpersonationError("impersonation requires a reason/ticket")
    session_id = ImpersonationSession(tenant_id=tenant_id, admin_user=admin_user, target_user=target_user,
                                      state="ACTIVE", read_only=read_only, reason=reason,
                                      ticket_ref=ticket_ref,
                                      expires_at=_now() + timedelta(minutes=ttl_minutes),
                                      started_at=_now())
    session.add(session_id)
    session.flush()
    audit(session, tenant_id, admin_user, "impersonation.started", resource_type="impersonation_session",
          resource_id=session_id.id, after={"target_user": target_user, "read_only": read_only,
                                            "expires_at": session_id.expires_at.isoformat()},
          reason=reason, correlation_id=request_id)
    outbox(session, "tenancy.impersonation.started.v1", tenant_id, request_id,
           {"tenant_id": str(tenant_id), "admin": admin_user, "target_user": target_user,
            "read_only": read_only})
    return session_id


def end_impersonation(session: Session, tenant_id, session_id: uuid.UUID, *, actor: str = "system") -> None:
    row = session.get(ImpersonationSession, session_id)
    if row is None or row.tenant_id != tenant_id:
        raise NotFoundError("impersonation session not found")
    row.state = "EXPIRED"
    row.ended_at = _now()
    session.flush()
    audit(session, tenant_id, actor, "impersonation.ended", resource_type="impersonation_session",
          resource_id=row.id, after={"state": "EXPIRED"})
