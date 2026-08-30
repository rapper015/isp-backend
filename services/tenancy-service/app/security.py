"""Tenancy-service security: management JWT + RBAC + tenant context resolution.

Tenant context is derived ONLY from the authenticated JWT claim (validated
against membership). A caller-supplied tenant_id must match the JWT claim; any
conflict is rejected. Missing context fails closed."""
import secrets as _secrets
from contextvars import ContextVar
from os import getenv

import jwt
from fastapi import HTTPException, Request

from .cache import limited
from .context import TenantContext, set_context

current_tenant: ContextVar[TenantContext | None] = ContextVar("tenancy_tenant_context", default=None)

from .domain.access import DEFAULT_ROLE_TEMPLATES  # noqa: E402


def _template_permissions(role: str) -> set[str]:
    return set(DEFAULT_ROLE_TEMPLATES.get(role, {}).get("permissions", []))


ROLE_PERMISSIONS = {role: _template_permissions(role) for role in DEFAULT_ROLE_TEMPLATES}
ROLE_SCOPES = {role: DEFAULT_ROLE_TEMPLATES.get(role, {}).get("scope", "TENANT") for role in DEFAULT_ROLE_TEMPLATES}

# Elevated permissions guard destructive/financial/tenant-wide operations.
ELEVATED_PERMISSIONS = {
    "tenants.activate": {"PLATFORM_ADMIN", "TENANT_ADMIN"},
    "tenants.suspend": {"PLATFORM_ADMIN", "ISP_ADMIN"},
    "tenants.offboard": {"PLATFORM_ADMIN"},
    "settlements.approve": {"FINANCE_MANAGER", "TENANT_ADMIN", "PLATFORM_ADMIN"},
    "settlements.reversal": {"FINANCE_MANAGER", "PLATFORM_ADMIN"},
    "wallet.adjust": {"FINANCE_MANAGER", "TENANT_ADMIN", "PLATFORM_ADMIN"},
    "payouts.record": {"FINANCE_MANAGER", "TENANT_ADMIN"},
    "impersonate": {"PLATFORM_ADMIN"},
    "commissions.plan.approve": {"FINANCE_MANAGER", "TENANT_ADMIN", "PLATFORM_ADMIN"},
}

PERMISSION_CATALOG = (
    "tenants.create", "tenants.view", "tenants.manage", "tenants.activate", "tenants.suspend",
    "tenants.offboard", "tenants.export", "tenants.health", "domains.manage", "config.manage",
    "feature.manage", "entitlements.manage", "quota.manage",
    "org.units.manage", "partners.create", "partners.manage", "partners.view",
    "agreements.manage", "agreements.approve", "ownership.manage", "ownership.transfer",
    "customers.view", "customers.create", "customers.own.view", "grants.manage",
    "memberships.manage", "roles.manage", "permissions.manage", "access.review",
    "service_accounts.manage", "impersonate",
    "commissions.manage", "commissions.calculate", "commissions.plan.approve",
    "settlements.manage", "settlements.calculate", "settlements.approve",
    "settlements.reversal", "payouts.record", "payouts.reconcile", "wallet.adjust",
    "reports.view", "reports.export", "reports.aggregate", "audit.view",
)


def _required_permission(method: str, path: str) -> str | None:
    if not path.startswith("/api/tenancy"):
        return None
    p = path.rstrip("/").split("?")[0]
    # tenant admin routes
    if "/tenants/" in p and ("activate" in p):
        return "tenants.activate"
    if "/tenants/" in p and ("suspend" in p or "restrict" in p or "resume" in p):
        return "tenants.suspend"
    if "/tenants/" in p and "offboard" in p:
        return "tenants.offboard"
    if "/tenants/" in p and "export" in p:
        return "tenants.export"
    if "/tenants/" in p and "health" in p:
        return "tenants.health"
    if p.endswith("/tenants") or "/tenants/" in p:
        return "tenants.manage" if method in ("POST", "PUT", "PATCH", "DELETE") else "tenants.view"
    if "/domains" in p:
        return "domains.manage" if method in ("POST", "PUT", "PATCH", "DELETE") else "tenants.view"
    if "/feature" in p or "/entitlement" in p or "/quota" in p or "/config" in p:
        return "feature.manage" if method in ("POST", "PUT", "PATCH", "DELETE") else "tenants.view"
    if "/org-units" in p:
        return "org.units.manage" if method in ("POST", "PUT", "PATCH", "DELETE") else "partners.view"
    if "/partners/" in p and "approve" in p:
        return "agreements.approve"
    if "/partners" in p:
        return "partners.manage" if method in ("POST", "PUT", "PATCH", "DELETE") else "partners.view"
    if "/agreements" in p:
        return "agreements.manage" if method in ("POST", "PUT", "PATCH") else "partners.view"
    if "/ownership" in p or "/transfers" in p:
        return "ownership.manage" if method in ("POST", "PUT", "PATCH") else "customers.view"
    if "/grants" in p:
        return "grants.manage" if method in ("POST", "PUT", "PATCH", "DELETE") else "customers.view"
    if "/permissions" in p or "/roles" in p or "/memberships" in p or "/access" in p:
        return "roles.manage" if method in ("POST", "PUT", "PATCH", "DELETE") else "permissions.manage"
    if "/service-accounts" in p or "/api-credentials" in p:
        return "service_accounts.manage"
    if "/impersonation" in p:
        return "impersonate"
    if "/commission" in p or "/commissions" in p:
        if "approve" in p:
            return "commissions.plan.approve"
        if "calculate" in p or "earning" in p or "clawback" in p or "adjustment" in p:
            return "commissions.calculate"
        return "commissions.manage" if method in ("POST", "PUT", "PATCH", "DELETE") else "commissions.manage"
    if "/settlement" in p or "/settlements" in p:
        if "approve" in p:
            return "settlements.approve"
        if "reverse" in p:
            return "settlements.reversal"
        if "payout" in p:
            return "payouts.record"
        if "reconcile" in p:
            return "payouts.reconcile"
        if "dispute" in p:
            return "settlements.manage"
        if "calculate" in p:
            return "settlements.calculate"
        return "settlements.manage" if method in ("POST", "PUT", "PATCH", "DELETE") else "settlements.manage"
    if "/wallet" in p:
        return "wallet.adjust" if method in ("POST", "PUT", "PATCH", "DELETE") else "settlements.manage"
    if "/reports" in p:
        if "aggregate" in p:
            return "reports.aggregate"
        if "export" in p:
            return "reports.export"
        return "reports.view" if method == "GET" else "reports.export"
    if "/audit" in p:
        return "audit.view"
    if "/governance" in p:
        return "governance.manage" if method in ("POST", "PUT", "PATCH", "DELETE") else "governance.view"
    return "tenants.view"


async def management_auth(request: Request) -> None:
    header = request.headers.get("Authorization", "")
    secret = getenv("TENANCY_JWT_SECRET", "")
    if not header.startswith("Bearer ") or not secret:
        raise HTTPException(401, "management authentication failed")
    if len(secret) < 32:
        raise HTTPException(503, "management authentication is not securely configured")
    try:
        claims = jwt.decode(header[7:], secret, algorithms=["HS256"])
    except jwt.PyJWTError as error:
        raise HTTPException(401, "invalid or expired management token") from error

    required = _required_permission(request.method, request.url.path)
    role = claims.get("role", "")
    permissions = set(claims.get("permissions", [])) | ROLE_PERMISSIONS.get(role, set())
    if required and "*" not in permissions and required not in permissions:
        raise HTTPException(403, "tenancy permission denied")
    if required in ELEVATED_PERMISSIONS and role not in ELEVATED_PERMISSIONS[required]:
        raise HTTPException(403, "elevated permission required")

    claimed_tenant = claims.get("tenant_id") or claims.get("tenantId")
    remote = request.client.host if request.client else "unknown"
    if not limited(f"tenancy:mgmt:{remote}:{request.url.path}",
                   int(getenv("TENANCY_RATE_LIMIT", "120")), 60):
        raise HTTPException(429, "rate limit exceeded")

    scope_kind = claims.get("scope_kind") or ROLE_SCOPES.get(role, "TENANT")
    ctx = TenantContext(
        tenant_id=_to_uuid(claimed_tenant) if claimed_tenant else None,
        user_id=claims.get("userId", claims.get("sub", "admin")),
        role=role,
        permissions=frozenset(permissions),
        scope_kind=scope_kind,
        correlation_id=request.headers.get("X-Correlation-Id"),
        impersonating_user=claims.get("impersonating_user"),
        auth_method="jwt",
    )
    request.state.tenancy_principal = {
        "subject": ctx.user_id, "role": role, "permissions": sorted(permissions),
        "tenant_id": str(ctx.tenant_id) if ctx.tenant_id else None, "scope_kind": scope_kind,
    }
    current_tenant.set(ctx)
    set_context(ctx)


async def internal_service_auth(request: Request) -> None:
    expected = getenv("TENANCY_INTERNAL_API_KEY", "")
    supplied = request.headers.get("X-Internal-API-Key", "")
    if not expected or not _secrets.compare_digest(expected, supplied):
        raise HTTPException(401, "internal service authentication failed")


def _to_uuid(value) -> None:
    from uuid import UUID
    try:
        return UUID(str(value))
    except (ValueError, TypeError):
        return None
