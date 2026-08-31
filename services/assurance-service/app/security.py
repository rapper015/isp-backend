"""Assurance-service security: management JWT + RBAC + tenant context."""
import secrets as _secrets
from contextvars import ContextVar
from os import getenv
from uuid import UUID

import jwt
from fastapi import HTTPException, Request

from .cache import limited
from .context import TenantContext, set_context

current_tenant: ContextVar[TenantContext | None] = ContextVar("assurance_tenant_context", default=None)

ROLE_PERMISSIONS = {
    "PLATFORM_ADMIN": {"*"},
    "ISP_OWNER": {"*"},
    "ISP_ADMIN": {"*"},
    "NOC_ENGINEER": {"alerts.view", "alerts.ack", "incidents.manage", "incidents.view",
                     "slo.view", "kpi.view", "synthetic.view", "dashboards.view"},
    "SRE_PLATFORM": {"alerts.manage", "alerts.ack", "incidents.manage", "incidents.declare",
                     "slo.manage", "slo.approve", "maintenance.manage", "kpi.manage",
                     "postmortem.manage", "root_cause.manage", "dashboards.manage"},
    "SRE_ENGINEER": {"alerts.view", "alerts.ack", "incidents.manage", "incidents.view",
                     "slo.view", "maintenance.manage", "postmortem.manage"},
    "SECURITY_OPS": {"alerts.view", "incidents.view", "root_cause.manage", "audit.view"},
    "AUDITOR": {"reports.view", "audit.view", "kpi.view", "slo.view", "dashboards.view"},
    "READ_ONLY": {"alerts.view", "incidents.view", "slo.view", "kpi.view", "dashboards.view"},
    "TENANT_ADMIN": {"alerts.view", "alerts.ack", "incidents.view", "slo.view", "kpi.view",
                     "synthetic.view", "dashboards.view", "maintenance.manage"},
    "FRANCHISE_ADMIN": {"alerts.view", "incidents.view", "slo.view", "kpi.view"},
    "super_admin": {"*"},
}

ELEVATED_PERMISSIONS = {
    "incidents.declare": {"NOC_ENGINEER", "SRE_PLATFORM", "ISP_ADMIN", "PLATFORM_ADMIN", "super_admin"},
    "slo.approve": {"SRE_PLATFORM", "PLATFORM_ADMIN", "super_admin"},
    "maintenance.approve": {"SRE_PLATFORM", "PLATFORM_ADMIN", "super_admin"},
    "postmortem.approve": {"SRE_PLATFORM", "PLATFORM_ADMIN", "super_admin"},
    "incidents.resolve": {"NOC_ENGINEER", "SRE_PLATFORM", "SRE_ENGINEER", "PLATFORM_ADMIN"},
    "reports.aggregate": {"PLATFORM_ADMIN", "SRE_PLATFORM", "super_admin"},
    "root_cause.confirm": {"SRE_PLATFORM", "SECURITY_OPS", "PLATFORM_ADMIN", "super_admin"},
}

PERMISSION_CATALOG = (
    "alerts.view", "alerts.ack", "alerts.manage", "incidents.view", "incidents.manage",
    "incidents.declare", "incidents.resolve", "slo.view", "slo.manage", "slo.approve",
    "kpi.view", "kpi.manage", "maintenance.manage", "maintenance.approve", "synthetic.view",
    "synthetic.manage", "postmortem.manage", "postmortem.approve", "root_cause.manage",
    "root_cause.confirm", "dashboards.view", "dashboards.manage", "reports.view",
    "reports.export", "reports.aggregate", "audit.view", "telemetry.ingest", "changes.view",
)


def _required_permission(method: str, path: str) -> str | None:
    if not path.startswith("/api/assurance"):
        return None
    p = path.rstrip("/").split("?")[0]
    if "/alerts" in p:
        if "ack" in p:
            return "alerts.ack"
        if method in ("POST", "PUT", "PATCH", "DELETE"):
            return "alerts.manage"
        return "alerts.view"
    if "/incidents" in p or "/incident" in p:
        if "resolve" in p:
            return "incidents.resolve"
        if "declare" in p or method == "POST":
            return "incidents.declare" if ("declare" in p or "/incidents" == p.rstrip("/")) else "incidents.manage"
        return "incidents.view" if method == "GET" else "incidents.manage"
    if "/postmortems" in p:
        if "approve" in p:
            return "postmortem.approve"
        return "postmortem.manage" if method in ("POST", "PUT", "PATCH") else "incidents.view"
    if "/root-causes" in p or "/root-cause" in p:
        if "confirm" in p:
            return "root_cause.confirm"
        return "root_cause.manage" if method in ("POST", "PUT", "PATCH") else "incidents.view"
    if "/slos" in p or "/slo" in p:
        if "approve" in p:
            return "slo.approve"
        if "activate" in p:
            return "slo.approve"
        return "slo.manage" if method in ("POST", "PUT", "PATCH") else "slo.view"
    if "/slis" in p:
        return "slo.manage" if method in ("POST", "PUT", "PATCH") else "slo.view"
    if "/maintenance" in p:
        if "approve" in p:
            return "maintenance.approve"
        return "maintenance.manage" if method in ("POST", "PUT", "PATCH") else "slo.view"
    if "/kpis" in p or "/kpi" in p:
        return "kpi.manage" if method in ("POST", "PUT", "PATCH") else "kpi.view"
    if "/synthetic" in p:
        return "synthetic.manage" if method in ("POST", "PUT", "PATCH") else "synthetic.view"
    if "/dashboards" in p:
        return "dashboards.manage" if method in ("POST", "PUT", "PATCH") else "dashboards.view"
    if "/changes" in p:
        return "changes.view"
    if "/telemetry" in p or "/observations" in p or "/sli-measurements" in p:
        return "telemetry.ingest"
    if "/reports" in p:
        if "aggregate" in p:
            return "reports.aggregate"
        if "export" in p:
            return "reports.export"
        return "reports.view"
    if "/audit" in p:
        return "audit.view"
    return "reports.view"


async def management_auth(request: Request) -> None:
    header = request.headers.get("Authorization", "")
    secret = getenv("ASSURANCE_JWT_SECRET", "")
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
        raise HTTPException(403, "assurance permission denied")
    if required in ELEVATED_PERMISSIONS and role not in ELEVATED_PERMISSIONS[required]:
        raise HTTPException(403, "elevated permission required")
    remote = request.client.host if request.client else "unknown"
    if not limited(f"assurance:mgmt:{remote}:{request.url.path}",
                   int(getenv("ASSURANCE_RATE_LIMIT", "120")), 60):
        raise HTTPException(429, "rate limit exceeded")
    claimed_tenant = claims.get("tenant_id") or claims.get("tenantId")
    ctx = TenantContext(
        tenant_id=_to_uuid(claimed_tenant) if claimed_tenant else None,
        user_id=claims.get("userId", claims.get("sub", "admin")),
        role=role,
        permissions=frozenset(permissions),
        scope_kind=claims.get("scope_kind") or ("PLATFORM_AGGREGATE" if role in ("PLATFORM_ADMIN", "super_admin") and not claimed_tenant else "TENANT"),
        correlation_id=request.headers.get("X-Correlation-Id"),
        auth_method="jwt",
    )
    request.state.assurance_principal = {"subject": ctx.user_id, "role": role,
                                         "permissions": sorted(permissions),
                                         "tenant_id": str(ctx.tenant_id) if ctx.tenant_id else None,
                                         "scope_kind": ctx.scope_kind}
    current_tenant.set(ctx)
    set_context(ctx)


async def internal_service_auth(request: Request) -> None:
    expected = getenv("ASSURANCE_INTERNAL_API_KEY", "")
    supplied = request.headers.get("X-Internal-API-Key", "")
    if not expected or not _secrets.compare_digest(expected, supplied):
        raise HTTPException(401, "internal service authentication failed")


def _to_uuid(value):
    try:
        return UUID(str(value))
    except (ValueError, TypeError):
        return None
