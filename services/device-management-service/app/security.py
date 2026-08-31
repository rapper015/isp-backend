"""Device-management security: management JWT + RBAC and internal service auth.
Tenant IDs are never trusted from the client alone; they are validated against
the authenticated principal. Sensitive operations (reboot, factory reset,
firmware, transfer, decommission) require elevated permissions."""
import secrets
from contextvars import ContextVar
from os import getenv

import jwt
from fastapi import HTTPException, Request

from .cache import limited

current_tenant: ContextVar[str | None] = ContextVar("device_current_tenant", default=None)

ROLE_PERMISSIONS = {
    "PLATFORM_ADMIN": {"*"},
    "ISP_OWNER": {"*"},
    "ISP_ADMIN": {"*"},
    "DEVICE_OPERATOR": {
        "device.view", "device.view_parameters", "device.refresh", "device.change_parameter",
        "device.apply_profile", "device.reboot", "device.run_diagnostics", "device.claim",
        "device.assign", "device.action.execute", "device.profile.manage", "device.view_audit",
    },
    "SUPPORT_AGENT": {
        "device.view", "device.view_parameters", "device.run_diagnostics", "device.view_audit",
    },
    "NOC_ENGINEER": {"device.view", "device.view_parameters", "device.refresh", "device.run_diagnostics"},
    "FIRMWARE_OPERATOR": {"device.view", "device.firmware.upload", "device.firmware.rollout",
                          "device.firmware.execute"},
    "FIRMWARE_APPROVER": {"device.view", "device.firmware.approve", "device.firmware.rollout",
                          "device.firmware.approve_stage"},
    "INVENTORY_CONTROLLER": {"device.view", "device.claim", "device.assign", "device.transfer"},
    "FIELD_TECHNICIAN": {"device.view", "device.claim", "device.assign"},
    "FIELD_SUPERVISOR": {"device.view", "device.claim", "device.assign", "device.transfer",
                         "device.reboot", "device.run_diagnostics"},
    "OSS_OPERATOR": {"device.view", "device.claim", "device.assign", "device.apply_profile",
                     "device.change_parameter"},
    "TENANT_ADMIN": {"device.*"},
    "AUDITOR": {"device.view", "device.view_audit", "device.export"},
    "READ_ONLY": {"device.view"},
    "super_admin": {"*"},
}

# Elevated permissions guard destructive/bulk operations.
ELEVATED_PERMISSIONS = {
    "device.factory_reset": {"DEVICE_OPERATOR", "ISP_ADMIN", "ISP_OWNER", "PLATFORM_ADMIN", "TENANT_ADMIN"},
    "device.firmware.execute": {"FIRMWARE_OPERATOR", "FIRMWARE_APPROVER", "ISP_ADMIN", "PLATFORM_ADMIN"},
    "device.transfer": {"FIELD_SUPERVISOR", "INVENTORY_CONTROLLER", "ISP_ADMIN", "PLATFORM_ADMIN"},
    "device.decommission": {"ISP_ADMIN", "PLATFORM_ADMIN"},
    "device.bulk_action": {"ISP_ADMIN", "PLATFORM_ADMIN"},
}


def _required_permission(method: str, path: str) -> str | None:
    if not path.startswith("/api/device-management"):
        return None
    parts = path.rstrip("/").split("/")
    # /devices/{id}/... sub-resources
    if "/devices/" in path:
        tail = parts[-1] if parts else ""
        if tail in ("reboot",):
            return "device.reboot"
        if tail == "factory-reset":
            return "device.factory_reset"
        if tail in ("claim",):
            return "device.claim"
        if tail == "transfer":
            return "device.transfer"
        if tail == "decommission":
            return "device.decommission"
        if tail == "diagnostics" or tail.startswith("diagnostics"):
            return "device.run_diagnostics" if method in ("POST", "PUT") else "device.view"
        if tail == "actions":
            return "device.action.execute" if method == "POST" else "device.view"
        if tail in ("refresh", "parameters"):
            return "device.refresh" if method in ("POST", "PUT") else "device.view_parameters"
        if tail in ("apply-profile", "configure", "reprovision"):
            return "device.apply_profile"
        if tail == "drift":
            return "device.view"
        return "device.view"
    if "/profiles" in path:
        return "device.profile.manage" if method in ("POST", "PUT", "DELETE") else "device.view"
    if "/firmware" in path:
        if tail_contains(path, ("approve",)):
            return "device.firmware.approve"
        if tail_contains(path, ("rollout", "cohort")) and method == "POST":
            return "device.firmware.rollout"
        if tail_contains(path, ("stages",)) and method == "POST":
            return "device.firmware.approve_stage"
        return "device.firmware.upload" if method == "POST" else "device.view"
    if "/diagnostics" in path:
        return "device.run_diagnostics" if method == "POST" else "device.view"
    if "/acs" in path:
        return "device.view"
    if "/reports" in path or "/audit" in path:
        return "device.view_audit" if "/audit" in path else "device.view"
    if "/devices" in path and method == "POST":
        return "device.claim"
    return "device.view"


def tail_contains(path: str, needles: tuple) -> bool:
    return any(n in path for n in needles)


def management_permission(method: str, path: str) -> str | None:
    return _required_permission(method, path)


async def management_auth(request: Request) -> None:
    header = request.headers.get("Authorization", "")
    secret = getenv("DEVICE_MANAGEMENT_JWT_SECRET", "")
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
        raise HTTPException(403, "device-management permission denied")
    # Elevated permission enforcement for destructive/bulk actions.
    if required in ELEVATED_PERMISSIONS and role not in ELEVATED_PERMISSIONS[required]:
        raise HTTPException(403, "elevated permission required")
    remote = request.client.host if request.client else "unknown"
    if not limited(f"device:management:{remote}:{request.url.path}",
                   int(getenv("DEVICE_MANAGEMENT_RATE_LIMIT", "120")), 60):
        raise HTTPException(429, "rate limit exceeded")
    claimed_tenant = claims.get("tenant_id")
    request.state.device_principal = {
        "subject": claims.get("userId", claims.get("sub", "admin")),
        "role": role,
        "permissions": sorted(permissions),
        "tenant_id": claimed_tenant,
    }
    current_tenant.set(claimed_tenant)


async def internal_service_auth(request: Request) -> None:
    expected = getenv("DEVICE_MANAGEMENT_INTERNAL_API_KEY", "")
    supplied = request.headers.get("X-Internal-API-Key", "")
    if not expected or not secrets.compare_digest(expected, supplied):
        raise HTTPException(401, "internal service authentication failed")
