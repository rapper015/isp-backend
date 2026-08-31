"""NMS security: internal service key auth + management JWT with RBAC."""
import secrets
from os import getenv

import jwt
from fastapi import HTTPException, Request

ROLE_PERMISSIONS = {
    "PLATFORM_ADMIN": {"*"},
    "ISP_OWNER": {"*"},
    "ISP_ADMIN": {"*"},
    "NOC_ENGINEER": {"nms.view", "nms.ops.view", "nms.ops.manage"},
    "SRE_PLATFORM": {"nms.view", "nms.ops.view", "nms.ops.manage"},
    "TENANT_ADMIN": {"nms.view", "nms.ops.view", "nms.ops.manage"},
    "AUDITOR": {"nms.view", "nms.ops.view"},
    "READ_ONLY": {"nms.view"},
    "super_admin": {"*"},
}


def management_permission(method: str, path: str) -> str | None:
    if not path.startswith("/api/nms"):
        return None
    if method in ("POST", "PUT", "PATCH", "DELETE"):
        return "nms.ops.manage"
    return "nms.ops.view" if "ops" in path else "nms.view"


async def management_auth(request: Request) -> None:
    header = request.headers.get("Authorization", "")
    secret = getenv("NMS_JWT_SECRET", "")
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
        raise HTTPException(403, "NMS permission denied")
    request.state.nms_principal = {
        "subject": claims.get("userId", claims.get("sub", "admin")),
        "role": role,
        "permissions": sorted(permissions),
        "tenant_id": claims.get("tenant_id") or claims.get("tenantId"),
    }


def internal_service_auth(request: Request) -> None:
    supplied = request.headers.get("X-NMS-Service-Key", "")
    configured = getenv("NMS_INTERNAL_API_KEYS", getenv("NMS_INTERNAL_API_KEY", ""))
    expected = [v.strip() for v in configured.split(",") if v.strip()]
    if not expected or not any(secrets.compare_digest(item, supplied) for item in expected):
        raise HTTPException(401, "internal service authentication failed")
