"""Workforce security: management JWT + RBAC + technician + internal key."""
import hmac
import os
from contextvars import ContextVar
from uuid import UUID

import jwt
from fastapi import HTTPException, Request

from .context import TenantContext

current_tenant: ContextVar[TenantContext | None] = ContextVar("workforce_tenant_context", default=None)

ROLE_PERMISSIONS = {
    "PLATFORM_ADMIN": {"*"},
    "ISP_OWNER": {"*"},
    "ISP_ADMIN": {"*"},
    "FIELD_MANAGER": {
        "technicians.manage", "technicians.view", "workorders.manage", "workorders.view",
        "dispatch.manage", "inventory.manage", "inventory.view", "inventory.consume",
        "shifts.manage", "escalations.manage", "kpi.view", "dashboard.view",
        "fieldops.manage", "visits.manage", "proof.manage", "feedback.view",
        "sla.view", "audit.view", "location.ingest",
    },
    "DISPATCHER": {
        "workorders.view", "workorders.manage", "dispatch.manage", "technicians.view",
        "shifts.view", "escalations.manage", "dashboard.view", "location.ingest",
    },
    "FIELD_TECHNICIAN": {
        "workorders.view", "fieldops.manage", "visits.manage", "proof.manage",
        "inventory.view", "inventory.consume", "feedback.view", "location.ingest",
    },
    "TENANT_ADMIN": {
        "technicians.manage", "technicians.view", "workorders.manage", "workorders.view",
        "dispatch.manage", "inventory.manage", "inventory.view", "inventory.consume",
        "shifts.manage", "escalations.manage", "kpi.view", "dashboard.view",
        "fieldops.manage", "visits.manage", "proof.manage", "feedback.view",
        "sla.view", "audit.view", "location.ingest",
    },
    "AUDITOR": {"workorders.view", "technicians.view", "kpi.view", "sla.view",
                "feedback.view", "dashboard.view", "audit.view"},
    "READ_ONLY": {"workorders.view", "technicians.view", "kpi.view", "dashboard.view"},
    "super_admin": {"*"},
}

PERMISSION_CATALOG = (
    "technicians.manage", "technicians.view", "workorders.manage", "workorders.view",
    "dispatch.manage", "inventory.manage", "inventory.view", "inventory.consume",
    "shifts.manage", "shifts.view", "escalations.manage", "kpi.view", "dashboard.view",
    "fieldops.manage", "visits.manage", "proof.manage", "feedback.view",
    "location.ingest", "sla.view",
)

INTERNAL_ROUTES = ("/api/workforce/v1/internal/",)


def _secret() -> str:
    s = os.getenv("WORKFORCE_JWT_SECRET")
    if not s or len(s) < 32:
        raise HTTPException(status_code=503, detail="WORKFORCE_JWT_SECRET not configured")
    return s


def internal_key_ok(key: str | None) -> bool:
    expected = os.getenv("WORKFORCE_INTERNAL_API_KEY")
    return bool(expected) and bool(key) and hmac.compare_digest(expected, key)


def _decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, _secret(), algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid token: {exc}")


def _required_permission(method: str, path: str) -> str | None:
    if not path.startswith("/api/workforce"):
        return None
    p = path.rstrip("/").split("?")[0]
    if "/work-orders" in p or "/workorders" in p:
        if method in ("POST", "PUT", "PATCH") or "/complete" in p or "/transition" in p \
                or "/escalate" in p or "/assign" in p or "/dispatch" in p:
            return "workorders.manage"
        return "workorders.view"
    if "/technicians" in p:
        if "/status" in p or method in ("POST", "PUT", "PATCH"):
            return "technicians.manage"
        return "technicians.view"
    if "/dispatch" in p:
        return "dispatch.manage"
    if "/inventory" in p or "/consumables" in p or "/spare" in p:
        if "/consume" in p or "/use" in p:
            return "inventory.consume"
        if method in ("POST", "PUT", "PATCH") or "/issue" in p or "/return" in p or "/sync" in p:
            return "inventory.manage"
        return "inventory.view"
    if "/shifts" in p:
        return "shifts.manage" if method in ("POST", "PUT", "PATCH") else "shifts.view"
    if "/escalations" in p:
        return "escalations.manage"
    if "/kpi" in p:
        return "kpi.view"
    if "/feedback" in p:
        return "feedback.view" if method == "GET" else "workorders.manage"
    if "/visits" in p or "/proof" in p:
        return "visits.manage"
    if "/site-checks" in p or "/handover" in p or "/checklist" in p:
        return "fieldops.manage"
    if "/dashboard" in p:
        return "dashboard.view"
    if "/sla" in p:
        return "sla.view"
    if "/location" in p:
        return "location.ingest"
    if "/expert" in p or "/visualization" in p or "/overlay" in p:
        return "workorders.manage" if method in ("POST", "PUT", "PATCH") else "workorders.view"
    return None


def get_auth_context(request: Request) -> TenantContext:
    path = request.url.path
    if any(path.startswith(r) for r in INTERNAL_ROUTES):
        key = request.headers.get("X-Internal-API-Key")
        if internal_key_ok(key):
            return TenantContext(user_id="system:internal", role="DISPATCHER",
                                 scope_kind="PLATFORM_AGGREGATE",
                                 is_platform_aggregate=True,
                                 permissions=set(ROLE_PERMISSIONS["DISPATCHER"]))
        raise HTTPException(status_code=401, detail="Invalid internal API key")
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    claims = _decode_token(auth[7:])
    role = claims.get("role", "READ_ONLY")
    perms = set(ROLE_PERMISSIONS.get(role, set(ROLE_PERMISSIONS["READ_ONLY"])))
    if claims.get("permissions"):
        perms |= set(claims["permissions"])
    tenant_raw = claims.get("tenant_id")
    scope_kind = claims.get("scope_kind")
    ctx = TenantContext(
        user_id=claims.get("userId", "unknown"),
        role=role,
        tenant_id=UUID(tenant_raw) if tenant_raw else None,
        permissions=perms,
        scope_kind=scope_kind,
        is_platform_aggregate=(scope_kind == "PLATFORM_AGGREGATE" or role in ("PLATFORM_ADMIN", "super_admin")),
    )
    current_tenant.set(ctx)
    return ctx


def require_permission(ctx: TenantContext, perm: str) -> None:
    if "*" not in ctx.permissions and perm not in ctx.permissions:
        raise HTTPException(status_code=403, detail=f"Missing permission: {perm}")
