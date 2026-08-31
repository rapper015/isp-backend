"""Auth for the Warehouse service (JWT management + internal key)."""
from os import getenv

import jwt
from fastapi import Depends, Header, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_SECRET = getenv("WAREHOUSE_JWT_SECRET", "change-me-warehouse-jwt-secret-0123456789abcdef")
_INTERNAL_KEYS = {k for k in getenv("WAREHOUSE_INTERNAL_API_KEYS", getenv("WAREHOUSE_INTERNAL_API_KEY", "test-internal-key")).split(",") if k}

_bearer = HTTPBearer(auto_error=False)

ROLE_PERMISSIONS = {
    "DATA_ANALYST": {"analytics.view", "analytics.manage"},
    "BI_ANALYST": {"analytics.view", "analytics.manage"},
    "FINANCE_OPS": {"analytics.view", "analytics.manage"},
    "TENANT_ADMIN": {"analytics.view", "analytics.manage"},
    "AUDITOR": {"analytics.view"},
    "READ_ONLY": {"analytics.view"},
    "PLATFORM_ADMIN": {"analytics.view", "analytics.manage"},
}


def _required_permission(method: str, path: str) -> str:
    if method in ("POST", "PUT", "PATCH", "DELETE"):
        return "analytics.manage"
    return "analytics.view"


def management_auth(request: Request, creds: HTTPAuthorizationCredentials | None = Depends(_bearer)):
    if creds is None:
        raise HTTPException(status_code=401, detail="Missing bearer token")
    try:
        claims = jwt.decode(creds.credentials, _SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
    role = claims.get("role", "READ_ONLY")
    perms = set(claims.get("permissions") or []) | ROLE_PERMISSIONS.get(role, set())
    need = _required_permission(request.method, request.url.path)
    if need not in perms and role not in ("PLATFORM_ADMIN",):
        raise HTTPException(status_code=403, detail=f"Missing permission: {need}")
    request.state.wh_principal = {"role": role, "tenant_id": claims.get("tenant_id"), "userId": claims.get("userId")}
    return request.state.wh_principal


def internal_service_auth(x_warehouse_service_key: str = Header(default="")):
    if x_warehouse_service_key not in _INTERNAL_KEYS:
        raise HTTPException(status_code=401, detail="Invalid service key")
