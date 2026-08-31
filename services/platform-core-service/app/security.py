import hashlib, secrets, threading, time, uuid
from datetime import datetime, timedelta, timezone
from os import getenv
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import HTTPException, Request

PASSWORDS = PasswordHasher()
ISSUER = "isp-platform"
_rate_lock = threading.Lock()
_rate_windows: dict[str, tuple[float, int]] = {}
def limited(key: str, limit: int, window_seconds: int = 60) -> bool:
    """Process-local guard for auth endpoints; deploy Valkey-backed limits at edge scale."""
    now = time.monotonic()
    with _rate_lock:
        started, used = _rate_windows.get(key, (now, 0))
        if now - started >= window_seconds: started, used = now, 0
        if used >= limit: return False
        _rate_windows[key] = (started, used + 1)
        return True
def _secret():
    value = getenv("PLATFORM_JWT_SECRET", "")
    if len(value) < 32: raise RuntimeError("PLATFORM_JWT_SECRET must be at least 32 characters")
    return value
def hash_password(value): return PASSWORDS.hash(value)
def verify_password(value, hashed):
    try: return PASSWORDS.verify(hashed, value)
    except VerifyMismatchError: return False
def token_hash(value): return hashlib.sha256(value.encode()).hexdigest()
def issue_access_token(user, roles, permissions):
    now = datetime.now(timezone.utc); ttl = int(getenv("PLATFORM_ACCESS_TOKEN_TTL_SECONDS", "900"))
    return jwt.encode({"sub": str(user.id), "tenant_id": str(user.tenant_id) if user.tenant_id else None,
        "roles": sorted(roles), "permissions": sorted(permissions), "token_type": "access", "jti": str(uuid.uuid4()),
        "iat": now, "exp": now + timedelta(seconds=ttl), "iss": ISSUER}, _secret(), algorithm="HS256")
def issue_service_access_token(account_id, tenant_id, permissions):
    now = datetime.now(timezone.utc); ttl = int(getenv("PLATFORM_SERVICE_TOKEN_TTL_SECONDS", "300"))
    return jwt.encode({"sub": str(account_id), "tenant_id": str(tenant_id) if tenant_id else None,
        "roles": ["SERVICE_ACCOUNT"], "permissions": sorted(permissions), "token_type": "access", "jti": str(uuid.uuid4()),
        "iat": now, "exp": now + timedelta(seconds=ttl), "iss": ISSUER}, _secret(), algorithm="HS256")
def decode_access_token(value):
    try:
        claims = jwt.decode(value, _secret(), algorithms=["HS256"], issuer=ISSUER, options={"require": ["sub", "exp", "iat", "iss", "jti"]})
    except jwt.PyJWTError as exc: raise HTTPException(401, "invalid or expired platform access token") from exc
    if claims.get("token_type") != "access": raise HTTPException(401, "invalid token type")
    return claims
def bearer_claims(request: Request):
    value = request.headers.get("Authorization", "")
    if not value.startswith("Bearer "): raise HTTPException(401, "missing bearer token")
    return decode_access_token(value[7:])
def new_refresh_token(): return secrets.token_urlsafe(48)
