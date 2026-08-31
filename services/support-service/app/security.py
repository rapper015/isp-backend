"""Support security: management JWT + RBAC, customer portal JWT, internal
service auth and rate limiting. Mirrors the OSS/CRM conventions with support
permissions. Tenant/customer IDs are never trusted from the client alone —
they are validated against the authenticated principal."""
import secrets
from contextvars import ContextVar
from os import getenv

import jwt
from fastapi import HTTPException, Request

from .cache import limited

# Holds the authenticated principal's tenant for the current request so service
# code can resolve tenant scope without trusting a client-supplied value.
current_tenant: ContextVar[str | None] = ContextVar("support_current_tenant", default=None)

ROLE_PERMISSIONS = {
    "PLATFORM_ADMIN": {"*"},
    "ISP_OWNER": {"*"},
    "ISP_ADMIN": {"*"},
    "SUPPORT_MANAGER": {
        "support.ticket.view", "support.ticket.create", "support.ticket.assign", "support.ticket.transfer",
        "support.ticket.escalate", "support.ticket.internal_note", "support.ticket.public_reply",
        "support.ticket.resolve", "support.ticket.close", "support.ticket.reopen", "support.ticket.cancel",
        "support.ticket.mark_duplicate", "support.billing.summary.view", "support.diagnostic.view",
        "support.diagnostic.run", "support.action.request", "support.action.approve", "support.action.execute",
        "support.outage.link", "support.sla.manage", "support.catalog.manage", "support.routing.manage",
        "support.kb.manage", "support.report.view", "support.audit.view", "support.export",
    },
    "SUPERVISOR": {
        "support.ticket.view", "support.ticket.create", "support.ticket.assign", "support.ticket.transfer",
        "support.ticket.escalate", "support.ticket.internal_note", "support.ticket.public_reply",
        "support.ticket.resolve", "support.ticket.close", "support.ticket.reopen", "support.ticket.cancel",
        "support.ticket.mark_duplicate", "support.billing.summary.view", "support.diagnostic.view",
        "support.diagnostic.run", "support.action.request", "support.action.approve", "support.outage.link",
        "support.report.view", "support.audit.view",
    },
    "L2_SUPPORT": {
        "support.ticket.view", "support.ticket.create", "support.ticket.assign", "support.ticket.transfer",
        "support.ticket.escalate", "support.ticket.internal_note", "support.ticket.public_reply",
        "support.ticket.resolve", "support.ticket.close", "support.ticket.reopen", "support.ticket.cancel",
        "support.ticket.mark_duplicate", "support.billing.summary.view", "support.diagnostic.view",
        "support.diagnostic.run", "support.action.request", "support.outage.link",
    },
    "L1_SUPPORT": {
        "support.ticket.view", "support.ticket.create", "support.ticket.assign", "support.ticket.transfer",
        "support.ticket.escalate", "support.ticket.internal_note", "support.ticket.public_reply",
        "support.ticket.resolve", "support.ticket.close", "support.ticket.reopen", "support.ticket.cancel",
        "support.ticket.mark_duplicate", "support.diagnostic.view", "support.action.request",
    },
    "NOC_ENGINEER": {
        "support.ticket.view", "support.ticket.escalate", "support.ticket.internal_note",
        "support.diagnostic.view", "support.diagnostic.run", "support.action.request", "support.outage.link",
    },
    "BILLING_SUPPORT": {
        "support.ticket.view", "support.ticket.internal_note", "support.ticket.public_reply",
        "support.billing.summary.view", "support.action.request", "support.ticket.resolve",
    },
    "FIELD_COORDINATOR": {
        "support.ticket.view", "support.ticket.internal_note", "support.action.request", "support.ticket.resolve",
    },
    "CUSTOMER_CARE": {
        "support.ticket.view", "support.ticket.create", "support.ticket.internal_note", "support.ticket.public_reply",
        "support.ticket.resolve", "support.ticket.close",
    },
    "AUDITOR": {
        "support.ticket.view", "support.report.view", "support.audit.view", "support.export",
    },
    "READ_ONLY": {"support.ticket.view", "support.report.view"},
    "super_admin": {"*"},
}


def management_permission(method: str, path: str) -> str | None:
    if not path.startswith("/api/support"):
        return None
    if path.endswith("/valid-actions") or "/events" in path or "/history" in path:
        return "support.ticket.view"
    if "/portal/" in path:
        return None  # portal auth handles these
    # knowledge
    if "/knowledge" in path:
        return "support.kb.manage" if method in ("POST", "PUT", "DELETE", "PATCH") else "support.ticket.view"
    # SLA policies
    if "/sla/policies" in path:
        return "support.sla.manage" if method in ("POST", "PUT", "PATCH", "DELETE") else "support.ticket.view"
    if "/sla/override" in path:
        return "support.sla.manage"
    # routing / agents / catalog
    if "/routing" in path or "/agents" in path or "/catalog" in path or "/queues" in path or "/teams" in path:
        return "support.routing.manage" if method in ("POST", "PUT", "PATCH", "DELETE") else "support.ticket.view"
    # actions
    if "/actions/preview" in path:
        return "support.action.request"
    if "/actions/" in path:
        if path.endswith("/approve"):
            return "support.action.approve"
        if path.endswith("/execute") or path.endswith("/retry"):
            return "support.action.execute"
        return "support.action.request"
    # diagnostics
    if "/diagnostics" in path:
        if path.endswith("/refresh"):
            return "support.diagnostic.run"
        return "support.diagnostic.view"
    # outages / incidents
    if "/outages" in path or "/incidents" in path:
        return "support.outage.link" if method in ("POST", "PUT", "DELETE") else "support.ticket.view"
    # CSAT / reports / audit
    if "/csat" in path or "/reports" in path:
        return "support.report.view"
    if "/audit" in path:
        return "support.audit.view"
    if "/billing" in path:
        return "support.billing.summary.view" if method == "GET" else "support.ticket.view"
    # ticket commands
    if "/tickets/" in path:
        if method == "POST":
            if path.endswith("/assign") or path.endswith("/reassign"):
                return "support.ticket.assign"
            if path.endswith("/transfer"):
                return "support.ticket.transfer"
            if path.endswith("/escalate"):
                return "support.ticket.escalate"
            if path.endswith("/note"):
                return "support.ticket.internal_note"
            if path.endswith("/reply"):
                return "support.ticket.public_reply"
            if path.endswith("/resolve"):
                return "support.ticket.resolve"
            if path.endswith("/close") or path.endswith("/confirm"):
                return "support.ticket.close"
            if path.endswith("/reopen"):
                return "support.ticket.reopen"
            if path.endswith("/cancel"):
                return "support.ticket.cancel"
            if path.endswith("/duplicate"):
                return "support.ticket.mark_duplicate"
            return "support.ticket.view"
        return "support.ticket.view"
    if "/tickets" == path.rstrip("/"):
        return "support.ticket.create" if method == "POST" else "support.ticket.view"
    return "support.ticket.view"


async def _json_tenant(request: Request) -> str | None:
    try:
        body = await request.json()
    except Exception:
        return None
    return body.get("tenant_id") or body.get("tenantId")


async def management_auth(request: Request) -> None:
    header = request.headers.get("Authorization", "")
    secret = getenv("SUPPORT_JWT_SECRET", "")
    if not header.startswith("Bearer ") or not secret:
        raise HTTPException(401, "management authentication failed")
    if len(secret) < 32:
        raise HTTPException(503, "management authentication is not securely configured")
    try:
        claims = jwt.decode(header[7:], secret, algorithms=["HS256"])
    except jwt.PyJWTError as error:
        raise HTTPException(401, "invalid or expired management token") from error
    required = management_permission(request.method, request.url.path)
    role = claims.get("role", "")
    permissions = set(claims.get("permissions", [])) | ROLE_PERMISSIONS.get(role, set())
    if required and "*" not in permissions and required not in permissions:
        raise HTTPException(403, "support permission denied")
    claimed_tenant = claims.get("tenant_id") or claims.get("tenantId")
    if claimed_tenant and role not in {"PLATFORM_ADMIN", "ISP_OWNER", "ISP_ADMIN", "super_admin"}:
        supplied = request.query_params.get("tenant_id") or (await _json_tenant(request))
        if supplied and not secrets.compare_digest(str(claimed_tenant), str(supplied)):
            raise HTTPException(403, "tenant access denied")
    remote = request.client.host if request.client else "unknown"
    if not limited(f"support:management:{remote}:{request.url.path}", int(getenv("SUPPORT_MANAGEMENT_RATE_LIMIT", "120")), 60):
        raise HTTPException(429, "rate limit exceeded")
    request.state.support_principal = {
        "subject": claims.get("userId", claims.get("sub", "admin")),
        "role": role,
        "permissions": sorted(permissions),
        "tenant_id": claimed_tenant,
    }
    current_tenant.set(claimed_tenant)


def portal_principal(request: Request) -> dict:
    principal = getattr(request.state, "support_portal_principal", None)
    if principal is None:
        raise HTTPException(401, "customer authentication required")
    return principal


async def customer_auth(request: Request) -> None:
    header = request.headers.get("Authorization", "")
    secret = getenv("SUPPORT_CUSTOMER_JWT_SECRET", "")
    if not header.startswith("Bearer ") or not secret:
        raise HTTPException(401, "customer authentication failed")
    try:
        claims = jwt.decode(header[7:], secret, algorithms=["HS256"])
    except jwt.PyJWTError as error:
        raise HTTPException(401, "invalid or expired customer token") from error
    if claims.get("role") not in ("CUSTOMER", "PORTAL_USER"):
        raise HTTPException(403, "customer role required")
    customer_id = claims.get("customer_id")
    tenant_id = claims.get("tenant_id") or claims.get("tenantId")
    if not customer_id or not tenant_id:
        raise HTTPException(401, "customer token missing identity")
    remote = request.client.host if request.client else "unknown"
    if not limited(f"support:portal:{remote}:{request.url.path}", int(getenv("SUPPORT_PORTAL_RATE_LIMIT", "60")), 60):
        raise HTTPException(429, "rate limit exceeded")
    request.state.support_portal_principal = {
        "customer_id": str(customer_id),
        "tenant_id": str(tenant_id),
        "subject": claims.get("sub", customer_id),
    }


def internal_service_auth(request: Request) -> None:
    secret = getenv("SUPPORT_INTERNAL_API_KEY", "")
    header = request.headers.get("X-Internal-API-Key", "")
    if not secret or not header:
        raise HTTPException(401, "internal service authentication failed")
    if not secrets.compare_digest(header, secret):
        raise HTTPException(401, "internal service authentication failed")
