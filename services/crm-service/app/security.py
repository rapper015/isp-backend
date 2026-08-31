"""CRM security: Fernet encryption for sensitive values, RBAC permissions,
internal service authentication and management JWT authentication. Mirrors the
AAA service conventions with CRM-specific permissions."""
import hashlib
import secrets
from os import getenv

import jwt
from cryptography.fernet import Fernet
from fastapi import HTTPException, Request

from .cache import limited


def _key() -> bytes:
    value = getenv("CRM_ENCRYPTION_KEY", "")
    if not value:
        raise RuntimeError("CRM_ENCRYPTION_KEY must be configured")
    return value.encode()


def encrypt_sensitive(value: str) -> str:
    return Fernet(_key()).encrypt(value.encode()).decode()


def decrypt_sensitive(value: str) -> str:
    return Fernet(_key()).decrypt(value.encode()).decode()


def hash_value(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def management_permission(method: str, path: str) -> str | None:
    if not path.startswith("/api/crm"):
        return None
    # Sensitive document access requires the sensitive-view permission.
    if "/documents/" in path and method == "GET":
        return "crm.document.view_sensitive"
    if path.endswith("/merge") or path.endswith("/merge-preview"):
        return "crm.customer.merge"
    if path.endswith("/risk/override"):
        return "crm.customer.risk_override"
    if "/kyc/" in path:
        if method == "POST":
            if path.endswith("/verify"):
                return "crm.kyc.verify"
            if path.endswith("/reject"):
                return "crm.kyc.reject"
            return "crm.kyc.submit"
        return "crm.kyc.view"
    if path.endswith("/transition") and "/customers/" in path:
        return "crm.customer.lifecycle_transition"
    if path.endswith("/convert"):
        return "crm.lead.convert"
    if path.endswith("/assign"):
        return "crm.lead.assign"
    if path.endswith("/transition"):
        return "crm.lead.transition"
    if path.endswith("/follow-ups") or "/follow-ups/" in path:
        return "crm.followup.manage" if method == "POST" or "/complete" in path or "/reschedule" in path else "crm.followup.manage"
    if "/leads" in path:
        return "crm.lead.view" if method == "GET" else "crm.lead.create" if method == "POST" else "crm.lead.transition"
    if "/audit" in path:
        return "crm.audit.view"
    if "/customers" in path or "/contacts" in path or "/addresses" in path or "/caf" in path:
        return "crm.customer.view" if method == "GET" else "crm.customer.create" if method == "POST" else "crm.customer.update"
    return "crm.customer.view"


async def _jwt_management_auth(request: Request) -> None:
    header = request.headers.get("Authorization", "")
    secret = getenv("PLATFORM_JWT_SECRET", "")
    if not header.startswith("Bearer ") or not secret:
        raise HTTPException(401, "management authentication failed")
    if len(secret) < 32:
        raise HTTPException(503, "management authentication is not securely configured")
    try:
        claims = jwt.decode(header[7:], secret, algorithms=["HS256"], issuer="isp-platform", options={"require": ["sub", "exp", "iat", "iss", "jti"]})
    except jwt.PyJWTError as error:
        raise HTTPException(401, "invalid or expired management token") from error
    required = management_permission(request.method, request.url.path)
    if claims.get("token_type") != "access":
        raise HTTPException(401, "invalid token type")
    permissions = set(claims.get("permissions", []))
    if required and "*" not in permissions and required not in permissions:
        raise HTTPException(403, "CRM permission denied")
    claimed_tenant = claims.get("tenant_id") or claims.get("tenantId")
    if claimed_tenant and "*" not in permissions:
        supplied = request.query_params.get("tenant_id") or (await _json_tenant(request))
        if supplied and not secrets.compare_digest(str(claimed_tenant), str(supplied)):
            raise HTTPException(403, "tenant access denied")
    remote = request.client.host if request.client else "unknown"
    if not limited(f"crm:management:{remote}:{request.url.path}", int(getenv("CRM_MANAGEMENT_RATE_LIMIT", "120")), 60):
        raise HTTPException(429, "rate limit exceeded")
    request.state.crm_principal = {"subject": claims["sub"], "roles": claims.get("roles", []), "permissions": sorted(permissions)}


async def _json_tenant(request: Request) -> str | None:
    try:
        body = await request.json()
        return body.get("tenant_id")
    except (ValueError, TypeError):
        return None


async def internal_service_auth(request: Request) -> None:
    supplied = request.headers.get("X-CRM-Service-Key", "")
    configured = getenv("CRM_INTERNAL_API_KEYS", getenv("CRM_INTERNAL_API_KEY", ""))
    expected = [value.strip() for value in configured.split(",") if value.strip()]
    service_key_valid = bool(expected and any(secrets.compare_digest(item, supplied) for item in expected))
    if not service_key_valid:
        if request.url.path.startswith(("/api/crm", "/internal")):
            if request.url.path.startswith(("/api/crm", "/internal/crm")):
                await _jwt_management_auth(request)
                return
        raise HTTPException(401, "internal service authentication failed")
    trusted = [value.strip() for value in getenv("CRM_TRUSTED_SOURCES", "").split(",") if value.strip()]
    if trusted and (not request.client or request.client.host not in trusted):
        raise HTTPException(403, "untrusted source")
    remote = request.client.host if request.client else "unknown"
    if not limited(f"crm:internal:{remote}:{request.url.path}", int(getenv("CRM_INTERNAL_RATE_LIMIT", "300")), 60):
        raise HTTPException(429, "rate limit exceeded")
