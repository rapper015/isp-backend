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

ROLE_PERMISSIONS = {
    "PLATFORM_ADMIN": {"*"},
    "ISP_OWNER": {"*"},
    "ISP_ADMIN": {"*"},
    "CRM_MANAGER": {
        "crm.lead.view", "crm.lead.create", "crm.lead.assign", "crm.lead.transition", "crm.lead.convert",
        "crm.customer.view", "crm.customer.create", "crm.customer.update", "crm.customer.merge",
        "crm.customer.lifecycle_transition", "crm.customer.risk_override",
        "crm.kyc.view", "crm.kyc.submit", "crm.document.view_sensitive",
        "crm.followup.manage", "crm.audit.view", "crm.export",
    },
    "SALES_MANAGER": {"crm.lead.view", "crm.lead.create", "crm.lead.assign", "crm.lead.transition", "crm.lead.convert", "crm.customer.view", "crm.customer.update", "crm.followup.manage", "crm.audit.view"},
    "SALES_AGENT": {"crm.lead.view", "crm.lead.create", "crm.lead.transition", "crm.customer.view", "crm.followup.manage"},
    "KYC_REVIEWER": {"crm.kyc.view", "crm.kyc.submit", "crm.kyc.verify", "crm.kyc.reject", "crm.document.view_sensitive", "crm.customer.view", "crm.audit.view"},
    "CUSTOMER_CARE": {"crm.customer.view", "crm.customer.update", "crm.lead.view", "crm.followup.manage"},
    "BRANCH_MANAGER": {"crm.lead.view", "crm.lead.create", "crm.lead.assign", "crm.lead.transition", "crm.customer.view", "crm.customer.update", "crm.followup.manage"},
    "FRANCHISE_ADMIN": {"crm.lead.view", "crm.lead.create", "crm.lead.assign", "crm.lead.transition", "crm.lead.convert", "crm.customer.view", "crm.customer.update", "crm.kyc.submit", "crm.followup.manage", "crm.audit.view"},
    "FRANCHISE_AGENT": {"crm.lead.view", "crm.lead.create", "crm.lead.transition", "crm.customer.view", "crm.followup.manage"},
    "ACCOUNT_MANAGER": {"crm.customer.view", "crm.customer.update", "crm.lead.view", "crm.followup.manage", "crm.customer.lifecycle_transition"},
    "AUDITOR": {"crm.audit.view", "crm.customer.view", "crm.lead.view"},
    "READ_ONLY": {"crm.customer.view", "crm.lead.view"},
    "super_admin": {"*"},
    "noc_admin": {"crm.customer.view", "crm.lead.view", "crm.audit.view"},
}


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
    secret = getenv("CRM_JWT_SECRET", "")
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
        raise HTTPException(403, "CRM permission denied")
    claimed_tenant = claims.get("tenant_id") or claims.get("tenantId")
    if claimed_tenant and role not in {"PLATFORM_ADMIN", "ISP_OWNER", "ISP_ADMIN", "super_admin"}:
        supplied = request.query_params.get("tenant_id") or (await _json_tenant(request))
        if supplied and not secrets.compare_digest(str(claimed_tenant), str(supplied)):
            raise HTTPException(403, "tenant access denied")
    remote = request.client.host if request.client else "unknown"
    if not limited(f"crm:management:{remote}:{request.url.path}", int(getenv("CRM_MANAGEMENT_RATE_LIMIT", "120")), 60):
        raise HTTPException(429, "rate limit exceeded")
    request.state.crm_principal = {"subject": claims.get("userId", claims.get("sub", "admin")), "role": role, "permissions": sorted(permissions)}


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
