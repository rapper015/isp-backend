"""OSS security: internal service key auth + management JWT with OSS RBAC.
Mirrors AAA/CRM conventions with OSS permissions."""
import secrets
from os import getenv

import jwt
from fastapi import HTTPException, Request

from .cache import limited

ROLE_PERMISSIONS = {
    "PLATFORM_ADMIN": {"*"},
    "ISP_OWNER": {"*"},
    "ISP_ADMIN": {"*"},
    "OSS_MANAGER": {
        "oss.order.view", "oss.order.create", "oss.order.submit", "oss.order.transition",
        "oss.order.cancel", "oss.order.retry", "oss.order.resume", "oss.order.compensate",
        "oss.order.manual_resolve", "oss.resource.view", "oss.resource.manage",
        "oss.subscription.view", "oss.subscription.manage", "oss.workflow.view",
        "oss.audit.view", "oss.export",
        "oss.asset.view", "oss.asset.manage", "oss.config.manage", "oss.vendor.manage",
        "oss.enterprise.manage", "oss.infra.view", "oss.infra.manage",
        "oss.security.manage", "oss.telemetry.ingest",
    },
    "OSS_OPERATOR": {
        "oss.order.view", "oss.order.create", "oss.order.submit", "oss.order.transition",
        "oss.order.cancel", "oss.order.retry", "oss.order.resume", "oss.resource.view",
        "oss.subscription.view", "oss.workflow.view", "oss.audit.view",
        "oss.asset.view", "oss.asset.manage", "oss.config.manage", "oss.telemetry.ingest",
        "oss.enterprise.manage",
    },
    "FULFILMENT_TEAM": {
        "oss.order.view", "oss.order.transition", "oss.order.retry", "oss.order.resume",
        "oss.order.manual_resolve", "oss.resource.view", "oss.subscription.view", "oss.workflow.view",
    },
    "NOC_TEAM": {"oss.order.view", "oss.resource.view", "oss.subscription.view", "oss.workflow.view"},
    "CUSTOMER_CARE": {"oss.order.view", "oss.order.cancel", "oss.subscription.view"},
    "AUDITOR": {"oss.order.view", "oss.resource.view", "oss.subscription.view", "oss.workflow.view", "oss.audit.view"},
    "READ_ONLY": {"oss.order.view", "oss.subscription.view"},
    "super_admin": {"*"},
    "noc_admin": {"oss.order.view", "oss.resource.view", "oss.subscription.view", "oss.workflow.view"},
}


def management_permission(method: str, path: str) -> str | None:
    if not path.startswith("/api/oss"):
        return None
    if path.endswith("/valid-actions"):
        return "oss.order.view"
    if "/events" in path or "/history" in path:
        return "oss.order.view"
    if path.endswith("/manual-interventions") and method == "POST":
        return "oss.order.manual_resolve"
    if "/manual-interventions" in path:
        return "oss.order.manual_resolve" if method == "POST" else "oss.workflow.view"
    if path.endswith("/compensate"):
        return "oss.order.compensate"
    if path.endswith("/resume"):
        return "oss.order.resume"
    if path.endswith("/retry"):
        return "oss.order.retry"
    if path.endswith("/cancel"):
        return "oss.order.cancel"
    if path.endswith("/submit"):
        return "oss.order.submit"
    if "/orders" in path:
        if method == "POST":
            return "oss.order.create"
        return "oss.order.view" if method == "GET" else "oss.order.transition"
    if "/reservations" in path or "/capacity" in path or "/resources" in path:
        return "oss.resource.manage" if method in ("POST", "PUT", "DELETE") else "oss.resource.view"
    if "/subscriptions" in path:
        return "oss.subscription.manage" if method in ("POST", "PUT", "DELETE") else "oss.subscription.view"
    if "/workflows" in path:
        return "oss.workflow.view"
    if "/assets" in path or "/splitters" in path or "/firmware" in path:
        return "oss.asset.manage" if method in ("POST", "PUT", "DELETE") else "oss.asset.view"
    if "/vendors" in path:
        return "oss.vendor.manage" if method == "POST" else "oss.asset.view"
    if "/config" in path or "/inventory/reconcile" in path:
        return "oss.config.manage"
    if "/enterprise" in path:
        return "oss.enterprise.manage"
    if "/infra" in path:
        return "oss.infra.manage" if method in ("POST", "PUT") else "oss.infra.view"
    if "/security/ddos" in path:
        return "oss.security.manage"
    if "/traffic" in path:
        return "oss.infra.manage" if method == "POST" else "oss.infra.view"
    if "/telemetry" in path:
        return "oss.telemetry.ingest"
    return "oss.order.view"


async def _json_tenant(request: Request) -> str | None:
    try:
        body = await request.json()
    except Exception:
        return None
    return body.get("tenant_id") or body.get("tenantId")


async def management_auth(request: Request) -> None:
    header = request.headers.get("Authorization", "")
    secret = getenv("OSS_JWT_SECRET", "")
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
        raise HTTPException(403, "OSS permission denied")
    claimed_tenant = claims.get("tenant_id") or claims.get("tenantId")
    if claimed_tenant and role not in {"PLATFORM_ADMIN", "ISP_OWNER", "ISP_ADMIN", "super_admin"}:
        supplied = request.query_params.get("tenant_id") or (await _json_tenant(request))
        if supplied and not secrets.compare_digest(str(claimed_tenant), str(supplied)):
            raise HTTPException(403, "tenant access denied")
    remote = request.client.host if request.client else "unknown"
    if not limited(f"oss:management:{remote}:{request.url.path}", int(getenv("OSS_MANAGEMENT_RATE_LIMIT", "120")), 60):
        raise HTTPException(429, "rate limit exceeded")
    request.state.oss_principal = {
        "subject": claims.get("userId", claims.get("sub", "admin")),
        "role": role,
        "permissions": sorted(permissions),
    }


def internal_service_auth(request: Request) -> None:
    secret = getenv("OSS_INTERNAL_API_KEY", "")
    header = request.headers.get("X-Internal-API-Key", "")
    if not secret or not header:
        raise HTTPException(401, "internal service authentication failed")
    if not secrets.compare_digest(header, secret):
        raise HTTPException(401, "internal service authentication failed")
