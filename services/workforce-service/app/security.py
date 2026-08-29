"""Workforce security: management JWT + RBAC, technician mobile JWT, customer
portal JWT and internal service auth. Tenant/customer/technician IDs are never
trusted from the client alone — they are validated against the authenticated
principal. Mobile tokens are short-lived; rate limiting is enforced."""
import secrets
from contextvars import ContextVar
from os import getenv

import jwt
from fastapi import HTTPException, Request

from .cache import limited

current_tenant: ContextVar[str | None] = ContextVar("workforce_current_tenant", default=None)

ROLE_PERMISSIONS = {
    "PLATFORM_ADMIN": {"*"},
    "ISP_OWNER": {"*"},
    "ISP_ADMIN": {"*"},
    "FIELD_SUPERVISOR": {
        "workforce.work.view", "workforce.work.assign", "workforce.work.reassign", "workforce.work.schedule",
        "workforce.work.dispatch", "workforce.work.complete", "workforce.work.cancel",
        "workforce.technician.view", "workforce.technician.manage", "workforce.dispatch.view",
        "workforce.dispatch.edit", "workforce.qa.review", "workforce.sla.manage", "workforce.inventory.view",
        "workforce.customer.contact.view", "workforce.gps.override", "workforce.audit.view", "workforce.export",
    },
    "DISPATCHER": {
        "workforce.work.view", "workforce.work.assign", "workforce.work.reassign", "workforce.work.schedule",
        "workforce.work.dispatch", "workforce.technician.view", "workforce.dispatch.view", "workforce.dispatch.edit",
        "workforce.customer.contact.view", "workforce.inventory.view",
    },
    "QA_REVIEWER": {
        "workforce.work.view", "workforce.qa.review", "workforce.work.complete", "workforce.proof.review",
        "workforce.audit.view",
    },
    "INVENTORY_CONTROLLER": {
        "workforce.work.view", "workforce.inventory.view", "workforce.inventory.manage", "workforce.audit.view",
    },
    "NOC_ENGINEER": {"workforce.work.view", "workforce.dispatch.view", "workforce.technician.view"},
    "SUPPORT_AGENT": {"workforce.work.view", "workforce.work.create", "workforce.customer.contact.view"},
    "OSS_OPERATOR": {"workforce.work.view", "workforce.work.create"},
    "FRANCHISE_OPERATOR": {"workforce.work.view", "workforce.work.assign", "workforce.dispatch.view"},
    "AUDITOR": {"workforce.work.view", "workforce.audit.view", "workforce.export", "workforce.report.view"},
    "READ_ONLY": {"workforce.work.view"},
    "super_admin": {"*"},
}


def management_permission(method: str, path: str) -> str | None:
    if not path.startswith("/api/workforce"):
        return None
    if "/portal/" in path:
        return None
    if "/technician/" in path:
        return None
    if path.endswith("/valid-actions") or "/events" in path or "/history" in path:
        return "workforce.work.view"
    if "/qa/" in path:
        return "workforce.qa.review" if method in ("POST", "PUT") else "workforce.work.view"
    if "/sla/" in path:
        return "workforce.sla.manage" if method in ("POST", "PUT", "PATCH", "DELETE") else "workforce.work.view"
    if "/inventory" in path:
        return "workforce.inventory.manage" if method in ("POST", "PUT", "DELETE") else "workforce.inventory.view"
    if "/dispatch/" in path:
        return "workforce.dispatch.edit" if method in ("POST", "PUT", "DELETE") else "workforce.dispatch.view"
    if "/technicians" in path or "/technician-profiles" in path:
        return "workforce.technician.manage" if method in ("POST", "PUT", "PATCH", "DELETE") else "workforce.technician.view"
    if "/reports" in path or "/export" in path:
        return "workforce.report.view" if "/export" not in path else "workforce.export"
    if "/audit" in path:
        return "workforce.audit.view"
    if "/proof" in path:
        return "workforce.proof.review" if method in ("POST", "PUT") else "workforce.work.view"
    if "/work-orders/" in path:
        if method == "POST":
            if path.endswith("/assign") or path.endswith("/reassign"):
                return "workforce.work.assign"
            if path.endswith("/schedule") or path.endswith("/reschedule"):
                return "workforce.work.schedule"
            if path.endswith("/dispatch"):
                return "workforce.work.dispatch"
            if path.endswith("/complete"):
                return "workforce.work.complete"
            if path.endswith("/cancel"):
                return "workforce.work.cancel"
            return "workforce.work.view"
        return "workforce.work.view"
    if path.rstrip("/").endswith("/work-orders"):
        return "workforce.work.create" if method == "POST" else "workforce.work.view"
    return "workforce.work.view"


async def _json_tenant(request: Request) -> str | None:
    try:
        body = await request.json()
    except Exception:
        return None
    return body.get("tenant_id") or body.get("tenantId")


async def management_auth(request: Request) -> None:
    header = request.headers.get("Authorization", "")
    secret = getenv("WORKFORCE_JWT_SECRET", "")
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
        raise HTTPException(403, "workforce permission denied")
    claimed_tenant = claims.get("tenant_id") or claims.get("tenantId")
    if claimed_tenant and role not in {"PLATFORM_ADMIN", "ISP_OWNER", "ISP_ADMIN", "super_admin"}:
        supplied = request.query_params.get("tenant_id") or (await _json_tenant(request))
        if supplied and not secrets.compare_digest(str(claimed_tenant), str(supplied)):
            raise HTTPException(403, "tenant access denied")
    remote = request.client.host if request.client else "unknown"
    if not limited(f"workforce:management:{remote}:{request.url.path}",
                   int(getenv("WORKFORCE_MANAGEMENT_RATE_LIMIT", "120")), 60):
        raise HTTPException(429, "rate limit exceeded")
    request.state.workforce_principal = {
        "subject": claims.get("userId", claims.get("sub", "admin")),
        "role": role,
        "permissions": sorted(permissions),
        "tenant_id": claimed_tenant,
    }
    current_tenant.set(claimed_tenant)


def technician_principal(request: Request) -> dict:
    principal = getattr(request.state, "workforce_technician_principal", None)
    if principal is None:
        raise HTTPException(401, "technician authentication required")
    return principal


async def technician_auth(request: Request) -> None:
    header = request.headers.get("Authorization", "")
    secret = getenv("WORKFORCE_TECHNICIAN_JWT_SECRET", "")
    if not header.startswith("Bearer ") or not secret:
        raise HTTPException(401, "technician authentication failed")
    try:
        claims = jwt.decode(header[7:], secret, algorithms=["HS256"])
    except jwt.PyJWTError as error:
        raise HTTPException(401, "invalid or expired technician token") from error
    if claims.get("role") != "TECHNICIAN":
        raise HTTPException(403, "technician role required")
    technician_id = claims.get("technician_id")
    tenant_id = claims.get("tenant_id") or claims.get("tenantId")
    device_ref = claims.get("device_ref")
    if not technician_id or not tenant_id:
        raise HTTPException(401, "technician token missing identity")
    remote = request.client.host if request.client else "unknown"
    if not limited(f"workforce:technician:{remote}:{request.url.path}",
                   int(getenv("WORKFORCE_TECHNICIAN_RATE_LIMIT", "180")), 60):
        raise HTTPException(429, "rate limit exceeded")
    request.state.workforce_technician_principal = {
        "technician_id": str(technician_id),
        "tenant_id": str(tenant_id),
        "device_ref": device_ref,
        "subject": claims.get("sub", technician_id),
    }
    current_tenant.set(str(tenant_id))


def customer_principal(request: Request) -> dict:
    principal = getattr(request.state, "workforce_customer_principal", None)
    if principal is None:
        raise HTTPException(401, "customer authentication required")
    return principal


async def customer_auth(request: Request) -> None:
    header = request.headers.get("Authorization", "")
    secret = getenv("WORKFORCE_CUSTOMER_JWT_SECRET", "")
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
    if not limited(f"workforce:customer:{remote}:{request.url.path}",
                   int(getenv("WORKFORCE_CUSTOMER_RATE_LIMIT", "60")), 60):
        raise HTTPException(429, "rate limit exceeded")
    request.state.workforce_customer_principal = {
        "customer_id": str(customer_id),
        "tenant_id": str(tenant_id),
        "subject": claims.get("sub", customer_id),
    }
    current_tenant.set(str(tenant_id))


def internal_service_auth(request: Request) -> None:
    secret = getenv("WORKFORCE_INTERNAL_API_KEY", "")
    header = request.headers.get("X-Internal-API-Key", "")
    if not secret or not header:
        raise HTTPException(401, "internal service authentication failed")
    if not secrets.compare_digest(header, secret):
        raise HTTPException(401, "internal service authentication failed")
