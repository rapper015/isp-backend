"""Intelligence-service security: management JWT + RBAC + tenant context."""
import secrets as _secrets
from contextvars import ContextVar
from os import getenv
from uuid import UUID

import jwt
from fastapi import HTTPException, Request

from .cache import limited
from .context import TenantContext, set_context

current_tenant: ContextVar[TenantContext | None] = ContextVar("intelligence_tenant_context", default=None)

ROLE_PERMISSIONS = {
    "PLATFORM_ADMIN": {"*"},
    "ISP_OWNER": {"*"},
    "ISP_ADMIN": {"*"},
    "AI_ENGINEER": {"datasets.view", "datasets.manage", "features.view", "features.manage",
                    "training.manage", "models.view", "models.manage", "predictions.view",
                    "monitoring.view"},
    "MLOPS_ENGINEER": {"training.manage", "models.manage", "models.approve", "deploy.manage",
                       "monitoring.view", "datasets.view"},
    "DATA_SCIENTIST": {"datasets.view", "features.view", "training.manage", "models.view",
                       "predictions.view", "monitoring.view"},
    "NOC_ENGINEER": {"predictions.view", "recommendations.view", "remediation.execute",
                     "monitoring.view", "fraud.view"},
    "SRE_PLATFORM": {"recommendations.view", "remediation.manage", "remediation.execute",
                     "kill_switch.manage", "monitoring.view", "models.view", "deploy.manage"},
    "SECURITY_OPS": {"fraud.manage", "fraud.view", "remediation.view", "monitoring.view"},
    "CRM_RETENTION": {"churn.view", "retention.manage", "recommendations.view"},
    "FINANCE_OPS": {"fraud.view", "reports.view", "recommendations.view"},
    "AUDITOR": {"reports.view", "audit.view", "monitoring.view"},
    "READ_ONLY": {"predictions.view", "recommendations.view", "monitoring.view", "fraud.view"},
    "TENANT_ADMIN": {"predictions.view", "recommendations.view", "fraud.view", "churn.view",
                     "maintenance.view", "remediation.view"},
    "FRANCHISE_ADMIN": {"predictions.view", "recommendations.view", "fraud.view", "churn.view"},
    "super_admin": {"*"},
}

ELEVATED_PERMISSIONS = {
    "models.approve": {"MLOPS_ENGINEER", "PLATFORM_ADMIN", "super_admin"},
    "deploy.manage": {"MLOPS_ENGINEER", "PLATFORM_ADMIN", "super_admin"},
    "kill_switch.manage": {"SRE_PLATFORM", "PLATFORM_ADMIN", "super_admin"},
    "remediation.manage": {"SRE_PLATFORM", "PLATFORM_ADMIN", "super_admin"},
    "fraud.manage": {"SECURITY_OPS", "PLATFORM_ADMIN", "super_admin"},
    "reports.aggregate": {"PLATFORM_ADMIN", "super_admin"},
    "retention.manage": {"CRM_RETENTION", "PLATFORM_ADMIN", "super_admin"},
}


def _required_permission(method: str, path: str) -> str | None:
    if not path.startswith("/api/intelligence") and not path.startswith("/api/v1/intelligence"):
        return None
    p = path.rstrip("/").split("?")[0]
    if "/contracts" in p:
        return "datasets.manage" if method in ("POST", "PUT", "PATCH") else "datasets.view"
    if "/datasets" in p:
        return "datasets.manage" if method in ("POST", "PUT", "PATCH") else "datasets.view"
    if "/features" in p:
        return "features.manage" if method in ("POST", "PUT", "PATCH") else "features.view"
    if "/training" in p:
        return "training.manage"
    if "/models" in p:
        if "approve" in p:
            return "models.approve"
        if "deploy" in p or "rollback" in p or "retire" in p:
            return "deploy.manage"
        return "models.manage" if method in ("POST", "PUT", "PATCH") else "models.view"
    if "/fraud" in p:
        return "fraud.manage" if method in ("POST", "PUT", "PATCH") else "fraud.view"
    if "/churn" in p or "/retention" in p:
        if "/retention" in p:
            return "retention.manage" if method in ("POST", "PUT", "PATCH") else "churn.view"
        return "churn.view"
    if "/maintenance" in p or "/capacity" in p:
        return "maintenance.view"
    if "/recommendations" in p:
        return "recommendations.view"
    if "/remediation" in p:
        if "kill" in p or "approve" in p or "reject" in p:
            return "remediation.manage"
        if "/execute" in p or "/complete" in p or "/fail" in p:
            return "remediation.execute"
        if method == "POST" and "/intents" in p:
            return "remediation.manage"
        return "remediation.view"
    if "/kill-switch" in p:
        return "kill_switch.manage"
    if "/monitoring" in p or "/drift" in p:
        return "monitoring.view"
    if "/reports" in p:
        if "aggregate" in p:
            return "reports.aggregate"
        return "reports.view"
    if "/audit" in p:
        return "audit.view"
    if "/predictions" in p:
        return "predictions.view"
    if "/insights" in p:
        return "predictions.view"
    return "predictions.view"


async def management_auth(request: Request) -> None:
    header = request.headers.get("Authorization", "")
    secret = getenv("INTELLIGENCE_JWT_SECRET", "")
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
        raise HTTPException(403, "intelligence permission denied")
    if required in ELEVATED_PERMISSIONS and role not in ELEVATED_PERMISSIONS[required]:
        raise HTTPException(403, "elevated permission required")
    remote = request.client.host if request.client else "unknown"
    if not limited(f"intelligence:mgmt:{remote}:{request.url.path}",
                   int(getenv("INTELLIGENCE_RATE_LIMIT", "120")), 60):
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
    request.state.intelligence_principal = {"subject": ctx.user_id, "role": role,
                                            "permissions": sorted(permissions),
                                            "tenant_id": str(ctx.tenant_id) if ctx.tenant_id else None,
                                            "scope_kind": ctx.scope_kind}
    current_tenant.set(ctx)
    set_context(ctx)


async def internal_service_auth(request: Request) -> None:
    expected = getenv("INTELLIGENCE_INTERNAL_API_KEY", "")
    supplied = request.headers.get("X-Internal-API-Key", "")
    if not expected or not _secrets.compare_digest(expected, supplied):
        raise HTTPException(401, "internal service authentication failed")


def _to_uuid(value):
    try:
        return UUID(str(value))
    except (ValueError, TypeError):
        return None
