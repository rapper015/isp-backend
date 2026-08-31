"""SIEM security: management JWT + RBAC + internal API key + audit capture."""
import os
from contextvars import ContextVar
from datetime import datetime, timezone
from uuid import UUID

import jwt
from fastapi import HTTPException, Request

from .context import TenantContext

current_tenant: ContextVar[TenantContext | None] = ContextVar("siem_tenant_context", default=None)

ROLE_PERMISSIONS = {
    "PLATFORM_ADMIN": {"*"},
    "ISP_OWNER": {"*"},
    "ISP_ADMIN": {"*"},
    "CISO": {"*"},
    "SECURITY_OPS": {
        "events.view", "events.ingest", "events.export", "evidence.view",
        "cases.view", "cases.manage", "cases.escalate", "cases.resolve",
        "violations.view", "violations.manage", "policies.manage",
        "retention.manage", "consent.view", "consent.manage", "dsar.manage",
        "audit.view", "audit.export", "li.approve",
        "reports.view", "reports.export", "vuln.manage", "dashboard.view",
    },
    "SOC_ANALYST": {
        "events.view", "events.export", "evidence.view",
        "cases.view", "cases.manage", "cases.escalate",
        "violations.view", "violations.manage", "dashboard.view",
    },
    "SOC_MANAGER": {
        "events.view", "events.export", "evidence.view",
        "cases.view", "cases.manage", "cases.escalate", "cases.resolve",
        "violations.view", "violations.manage", "dashboard.view",
        "reports.view", "reports.export",
    },
    "COMPLIANCE_OFFICER": {
        "events.view", "events.ingest", "events.export", "evidence.view", "audit.view", "audit.export",
        "policies.manage", "retention.manage", "consent.view", "consent.manage",
        "dsar.manage", "reports.view", "reports.export", "li.approve",
        "violations.view", "dashboard.view", "cases.manage",
    },
    "AUDITOR": {"audit.view", "audit.export", "reports.view", "reports.export",
                "events.view", "evidence.view", "dashboard.view"},
    "TENANT_ADMIN": {
        "events.view", "events.ingest", "events.export", "evidence.view",
        "cases.view", "cases.manage", "violations.view", "violations.manage",
        "consent.view", "consent.manage", "dsar.manage",
        "reports.view", "dashboard.view", "policies.manage", "retention.manage",
    },
    "READ_ONLY": {"events.view", "cases.view", "violations.view", "dashboard.view",
                  "reports.view", "audit.view", "consent.view"},
    "super_admin": {"*"},
}

PERMISSION_CATALOG = (
    "events.view", "events.ingest", "events.export", "evidence.view",
    "cases.view", "cases.manage", "cases.escalate", "cases.resolve",
    "violations.view", "violations.manage", "policies.manage", "retention.manage",
    "consent.view", "consent.manage", "dsar.manage", "audit.view", "audit.export",
    "li.approve", "reports.view", "reports.export", "vuln.manage", "dashboard.view",
)

# Internal ingest (feature 448 high-volume logging) bypasses JWT via API key.
INTERNAL_ROUTES = ("/api/siem/v1/internal/",)


def _required_permission(method: str, path: str) -> str | None:
    if not path.startswith("/api/siem"):
        return None
    p = path.rstrip("/").split("?")[0]
    if "/security-events" in p or p.endswith("/events"):
        if p.endswith("/export") or "/export" in p:
            return "events.export"
        if method == "POST" or "/ingest" in p:
            return "events.ingest"
        return "events.view"
    if "/evidence" in p:
        return "evidence.view"
    if "/cases" in p or "/case" in p:
        if "/escalate" in p:
            return "cases.escalate"
        if "/resolve" in p or "/transition" in p or method in ("POST", "PUT", "PATCH"):
            return "cases.manage"
        return "cases.view"
    if "/policies" in p:
        return "policies.manage"
    if "/violations" in p:
        return "violations.manage" if method in ("POST", "PUT", "PATCH") else "violations.view"
    if "/retention" in p:
        return "retention.manage"
    if "/consent" in p:
        return "consent.manage" if method == "POST" else "consent.view"
    if "/data-requests" in p or "/dsar" in p:
        return "dsar.manage"
    if "/audit-log" in p:
        return "audit.export" if "/export" in p else "audit.view"
    if "/li/" in p:
        return "li.approve" if method in ("POST", "PUT") else "events.view"
    if "/vulnerabilities" in p or "/vuln" in p:
        return "vuln.manage"
    if "/breach" in p:
        return "cases.manage"
    if "/compliance" in p:
        return "policies.manage" if method in ("POST", "PUT", "PATCH") else "violations.view"
    if "/playbooks" in p:
        return "cases.manage"
    if "/mfa" in p:
        return "events.ingest"
    if "/notices" in p:
        return "cases.manage" if method in ("POST", "PUT", "PATCH") else "cases.view"
    if "/forensics" in p:
        return "cases.manage" if method in ("POST", "PUT", "PATCH") else "cases.view"
    if "/dashboard" in p:
        return "dashboard.view"
    if "/regulatory" in p or "/reports" in p:
        return "reports.export" if method == "POST" else "reports.view"
    return None


def _secret() -> str:
    s = os.getenv("SIEM_JWT_SECRET")
    if not s or len(s) < 32:
        raise HTTPException(status_code=503, detail="SIEM_JWT_SECRET not configured")
    return s


def internal_key_ok(key: str | None) -> bool:
    expected = os.getenv("SIEM_INTERNAL_API_KEY")
    return bool(expected) and bool(key) and __import__("hmac").compare_digest(expected, key)


def _decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, _secret(), algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid token: {exc}")


def get_auth_context(request: Request) -> TenantContext:
    """Resolve the caller's tenant context; internal routes use the API key."""
    path = request.url.path
    if any(path.startswith(r) for r in INTERNAL_ROUTES):
        key = request.headers.get("X-Internal-API-Key")
        if internal_key_ok(key):
            return TenantContext(user_id="system:internal", role="SECURITY_OPS",
                                 scope_kind="PLATFORM_AGGREGATE",
                                 is_platform_aggregate=True,
                                 permissions=set(ROLE_PERMISSIONS["SECURITY_OPS"]))
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


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
