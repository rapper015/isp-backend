import hashlib
import secrets
from os import getenv
import jwt
from cryptography.fernet import Fernet
from fastapi import HTTPException, Request
from .cache import limited

def _key() -> bytes:
    value = getenv("AAA_ENCRYPTION_KEY", "")
    if not value: raise RuntimeError("AAA_ENCRYPTION_KEY must be configured")
    return value.encode()

def encrypt_secret(value: str) -> str: return Fernet(_key()).encrypt(value.encode()).decode()
def decrypt_secret(value: str) -> str: return Fernet(_key()).decrypt(value.encode()).decode()
def new_shared_secret() -> str: return secrets.token_urlsafe(32)
def hash_api_key(value: str) -> str: return hashlib.sha256(value.encode()).hexdigest()

ROLE_PERMISSIONS = {
    "super_admin": {"*"},
    "noc_admin": {"aaa.nas.view", "aaa.nas.manage", "aaa.nas.rotate_secret", "aaa.radius_server.view", "aaa.radius_server.manage", "aaa.subscriber_policy.view", "aaa.subscriber_policy.manage", "aaa.session.view", "aaa.session.disconnect", "aaa.session.coa", "aaa.accounting.view", "aaa.usage.view", "aaa.audit.view"},
    "billing_admin": {"aaa.subscriber_policy.view", "aaa.usage.view", "aaa.accounting.view"},
    "support_admin": {"aaa.subscriber_policy.view", "aaa.session.view", "aaa.accounting.view", "aaa.usage.view"},
}

def management_permission(method: str, path: str) -> str | None:
    if not path.startswith("/api/aaa/"): return None
    if "/nas" in path: return "aaa.nas.view" if method == "GET" else "aaa.nas.rotate_secret" if path.endswith("/rotate-secret") else "aaa.nas.manage"
    if "radius-server" in path: return "aaa.radius_server.view" if method == "GET" else "aaa.radius_server.manage"
    if "/sessions" in path: return "aaa.session.view" if method == "GET" else "aaa.session.coa" if path.endswith("/coa") else "aaa.session.disconnect"
    if "/accounting-events" in path: return "aaa.accounting.view" if method == "GET" else "aaa.accounting.replay"
    if "/usage" in path: return "aaa.usage.view"
    if "/subscribers" in path: return "aaa.subscriber_policy.view" if method == "GET" or path.endswith(("preview-policy", "test-eligibility")) else "aaa.session.coa" if path.endswith("/coa") else "aaa.session.disconnect" if path.endswith("/disconnect") else "aaa.subscriber_policy.manage"
    if "/credentials" in path or "/ip-pools" in path: return "aaa.secret.manage"
    if path.endswith("/tenants"): return "aaa.secret.manage"
    return "aaa.audit.view"

async def _jwt_management_auth(request: Request) -> None:
    header = request.headers.get("Authorization", "")
    secret = getenv("AAA_JWT_SECRET", "")
    if not header.startswith("Bearer ") or not secret: raise HTTPException(401, "management authentication failed")
    if len(secret) < 32: raise HTTPException(503, "management authentication is not securely configured")
    try: claims = jwt.decode(header[7:], secret, algorithms=["HS256"])
    except jwt.PyJWTError as error: raise HTTPException(401, "invalid or expired management token") from error
    required = management_permission(request.method, request.url.path)
    role = claims.get("role", "")
    permissions = set(claims.get("permissions", [])) | ROLE_PERMISSIONS.get(role, set())
    if required and "*" not in permissions and required not in permissions: raise HTTPException(403, "AAA permission denied")
    claimed_tenant = claims.get("tenant_id") or claims.get("tenantId")
    if claimed_tenant and role != "super_admin":
        try: supplied = request.query_params.get("tenant_id") or (await request.json()).get("tenant_id")
        except (ValueError, TypeError): supplied = None
        if supplied and not secrets.compare_digest(str(claimed_tenant), str(supplied)): raise HTTPException(403, "tenant access denied")
    remote = request.client.host if request.client else "unknown"
    if not limited(f"management:{remote}:{request.url.path}", int(getenv("AAA_MANAGEMENT_RATE_LIMIT", "120")), 60): raise HTTPException(429, "rate limit exceeded")
    request.state.aaa_principal = {"subject": claims.get("userId", claims.get("sub", "admin")), "role": role, "permissions": sorted(permissions)}

async def internal_service_auth(request: Request) -> None:
    supplied = request.headers.get("X-AAA-Service-Key", "")
    configured = getenv("AAA_INTERNAL_API_KEYS", getenv("AAA_INTERNAL_API_KEY", ""))
    expected = [value.strip() for value in configured.split(",") if value.strip()]
    service_key_valid = bool(expected and any(secrets.compare_digest(item, supplied) for item in expected))
    if not service_key_valid:
        if request.url.path.startswith("/api/aaa/"):
            await _jwt_management_auth(request)
            return
        raise HTTPException(401, "internal service authentication failed")
    trusted = [value.strip() for value in getenv("AAA_TRUSTED_SOURCES", "").split(",") if value.strip()]
    if trusted and (not request.client or request.client.host not in trusted): raise HTTPException(403, "untrusted source")
    mtls_identities = [value.strip() for value in getenv("AAA_MTLS_IDENTITIES", "").split(",") if value.strip()]
    if mtls_identities and request.headers.get("X-Client-Certificate-Identity", "") not in mtls_identities: raise HTTPException(401, "mTLS identity not allowed")
    remote = request.client.host if request.client else "unknown"
    if not limited(f"internal:{remote}:{request.url.path}", int(getenv("AAA_INTERNAL_RATE_LIMIT", "300")), 60): raise HTTPException(429, "rate limit exceeded")
