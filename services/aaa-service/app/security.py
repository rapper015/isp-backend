import hashlib
import secrets
from os import getenv
import jwt
import bcrypt
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

# --- Milestone 0: operator/user auth ----------------------------------------

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def platform_jwt_secret() -> str:
    """Login signs with PLATFORM_JWT_SECRET (falls back to AAA_JWT_SECRET).
    Services verify management JWTs with their own <SVC>_JWT_SECRET; set them
    all to the same value to make one login token work across the platform."""
    return getenv("PLATFORM_JWT_SECRET") or getenv("AAA_JWT_SECRET", "")


def issue_access_token(user_id, username: str, role: str, tenant_id=None,
                       expires_in_seconds: int | None = None) -> str:
    secret = platform_jwt_secret()
    if not secret or len(secret) < 32:
        raise HTTPException(503, "management authentication is not securely configured")
    ttl = int(getenv("AAA_TOKEN_TTL_SECONDS", "43200"))
    now = int(__import__("time").time())
    claims = {
        "userId": str(user_id),
        "username": username,
        "role": role,
        "permissions": sorted(ROLE_PERMISSIONS.get(role, set())),
        "iat": now,
        "exp": now + (expires_in_seconds or ttl),
    }
    if tenant_id:
        claims["tenant_id"] = str(tenant_id)
    return jwt.encode(claims, secret, algorithm="HS256")

ROLE_PERMISSIONS = {
    "super_admin": {"*"},
    "noc_admin": {"aaa.nas.view", "aaa.nas.manage", "aaa.nas.rotate_secret", "aaa.radius_server.view", "aaa.radius_server.manage", "aaa.subscriber_policy.view", "aaa.subscriber_policy.manage", "aaa.session.view", "aaa.session.disconnect", "aaa.session.coa", "aaa.accounting.view", "aaa.usage.view", "aaa.audit.view",
                   "nas.view", "nas.create", "nas.update", "nas.delete", "nas.enable", "nas.disable", "nas.decommission", "nas.credentials.manage", "nas.connection.test", "nas.discovery.run",
                   "nas.configuration.view", "nas.configuration.plan", "nas.configuration.approve", "nas.configuration.apply", "nas.configuration.rollback",
                   "nas.radius_assignment.manage", "nas.radius_secret.generate", "nas.radius_secret.view_once", "nas.radius_secret.rotate",
                   "nas.radius_registration.confirm", "nas.radius_registration.verify", "nas.drift.view", "nas.drift.reconcile", "nas.audit.view",
                   # Milestone 3 network control
                   "aaa.policy.view", "aaa.policy.manage", "aaa.policy.explain", "aaa.session.reapply", "aaa.session.force_reauth",
                   "aaa.control.view", "aaa.control.manage", "aaa.router.readiness", "aaa.router.manage", "aaa.fup.view", "aaa.fup.manage",
                   "aaa.ip.view", "aaa.ip.regulatory_lookup", "aaa.reconcile.run"},
    "network_operator": {"aaa.policy.view", "aaa.policy.explain", "aaa.session.view", "aaa.session.disconnect", "aaa.session.coa", "aaa.session.reapply",
                         "aaa.control.view", "aaa.control.manage", "aaa.router.readiness", "aaa.fup.view", "aaa.ip.view", "aaa.reconcile.run", "aaa.accounting.view", "aaa.audit.view"},
    "billing_admin": {"aaa.subscriber_policy.view", "aaa.usage.view", "aaa.accounting.view"},
    "support_admin": {"aaa.subscriber_policy.view", "aaa.session.view", "aaa.accounting.view", "aaa.usage.view"},
}

def management_permission(method: str, path: str) -> str | None:
    if path == "/api/nas" or path.startswith("/api/nas/"):
        if path == "/api/nas":
            return "nas.view" if method == "GET" else "nas.create" if method == "POST" else "nas.update"
        if method == "GET":
            return "nas.audit.view" if path.endswith("/audit") else "nas.configuration.view" if ("/snapshots" in path or "/jobs" in path or "/plans/" in path or path.endswith("/current-radius-configuration") or path.endswith("/desired-configuration") or path.endswith("/radius-registration-status")) else "nas.view"
        if method == "PATCH":
            return "nas.credentials.manage" if "/credentials" in path else "nas.radius_assignment.manage" if "radius-assignments" in path else "nas.update"
        if method == "DELETE":
            return "nas.radius_assignment.manage" if "radius-assignments" in path else "nas.delete"
        # POST below this point.
        if path.endswith("/enable"): return "nas.enable"
        if path.endswith("/disable"): return "nas.disable"
        if path.endswith("/decommission"): return "nas.decommission"
        if path.endswith("/credentials/rotate"): return "nas.credentials.manage"
        if path.endswith("/test-connection"): return "nas.connection.test"
        if path.endswith("/discover"): return "nas.discovery.run"
        if path.endswith("/approve"): return "nas.configuration.approve"
        if path.endswith("/cancel"): return "nas.configuration.apply"
        if path.endswith("/apply"): return "nas.configuration.apply"
        if path.endswith("/plan"): return "nas.configuration.plan"
        if path.endswith("/rollback"): return "nas.configuration.rollback"
        if path.endswith("/detect-drift"): return "nas.drift.view"
        if path.endswith("/reconcile"): return "nas.drift.reconcile"
        if path.endswith("/registration-package/reveal"): return "nas.radius_secret.view_once"
        if path.endswith("/registration-package"): return "nas.radius_secret.generate"
        if path.endswith("/rotate-secret") or path.endswith("/confirm-freeradius-update") or path.endswith("/apply-secret") or path.endswith("/rollback-secret"): return "nas.radius_secret.rotate"
        if path.endswith("/confirm-registration"): return "nas.radius_registration.confirm"
        if path.endswith("/verify"): return "nas.radius_registration.verify" if "/radius-assignments/" in path else "nas.configuration.apply"
        if "radius-assignments" in path: return "nas.radius_assignment.manage"
        return "nas.update"
    if not path.startswith("/api/aaa/"): return None
    if "/nas" in path: return "aaa.nas.view" if method == "GET" else "aaa.nas.rotate_secret" if path.endswith("/rotate-secret") else "aaa.nas.manage"
    if "radius-server" in path: return "aaa.radius_server.view" if method == "GET" else "aaa.radius_server.manage"
    if "/sessions" in path: return "aaa.session.view" if method == "GET" else "aaa.session.coa" if path.endswith("/coa") else "aaa.session.disconnect"
    if "/accounting-events" in path: return "aaa.accounting.view" if method == "GET" else "aaa.accounting.replay"
    if "/usage" in path: return "aaa.usage.view"
    if "/subscribers" in path: return "aaa.subscriber_policy.view" if method == "GET" or path.endswith(("preview-policy", "test-eligibility")) else "aaa.session.coa" if path.endswith("/coa") else "aaa.session.disconnect" if path.endswith("/disconnect") else "aaa.subscriber_policy.manage"
    if "/credentials" in path or "/ip-pools" in path: return "aaa.secret.manage"
    if path.endswith("/tenants"): return "aaa.secret.manage"
    # Milestone 3 network-control paths.
    if "/policies" in path or "/bandwidth-profiles" in path or "/traffic-classes" in path or "/qos-profiles" in path or "/fup-policies" in path:
        return "aaa.policy.manage" if method == "POST" else "aaa.policy.view"
    if "/policy-assignment" in path or "/overrides" in path: return "aaa.policy.manage"
    if path.endswith("/effective-policy/explain") or "effective-policy" in path: return "aaa.policy.explain" if method == "GET" or method == "POST" else "aaa.policy.view"
    if "/network/sessions" in path:
        if method == "GET": return "aaa.session.view"
        if path.endswith("/disconnect") or path.endswith("/disconnect-all"): return "aaa.session.disconnect"
        if path.endswith("/reapply"): return "aaa.session.reapply"
        if path.endswith("/force-reauth"): return "aaa.session.force_reauth"
        return "aaa.session.view"
    if "/control-actions" in path:
        return "aaa.control.manage" if method == "POST" or path.endswith(("/retry", "/cancel", "/outcome")) else "aaa.control.view"
    if "/network-readiness" in path or "/network-setup-requirements" in path: return "aaa.router.readiness"
    if "/managed-config" in path: return "aaa.router.manage" if method == "POST" else "aaa.router.readiness"
    if "/policy-drift" in path: return "aaa.router.readiness"
    if "/network/reconcile" in path: return "aaa.reconcile.run"
    if "/fup/" in path: return "aaa.fup.manage" if method == "POST" else "aaa.fup.view"
    if "/ip-identity/" in path: return "aaa.ip.regulatory_lookup" if path.endswith("/regulatory") else "aaa.ip.view"
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
        if request.url.path.startswith(("/api/aaa/", "/api/nas")):
            await _jwt_management_auth(request)
            return
        raise HTTPException(401, "internal service authentication failed")
    trusted = [value.strip() for value in getenv("AAA_TRUSTED_SOURCES", "").split(",") if value.strip()]
    if trusted and (not request.client or request.client.host not in trusted): raise HTTPException(403, "untrusted source")
    mtls_identities = [value.strip() for value in getenv("AAA_MTLS_IDENTITIES", "").split(",") if value.strip()]
    if mtls_identities and request.headers.get("X-Client-Certificate-Identity", "") not in mtls_identities: raise HTTPException(401, "mTLS identity not allowed")
    remote = request.client.host if request.client else "unknown"
    if not limited(f"internal:{remote}:{request.url.path}", int(getenv("AAA_INTERNAL_RATE_LIMIT", "300")), 60): raise HTTPException(429, "rate limit exceeded")
