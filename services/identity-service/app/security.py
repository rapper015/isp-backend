"""Identity security: bcrypt passwords, JWT issuance/verification, service key."""
import time
from os import getenv

import bcrypt
import jwt
from fastapi import HTTPException, Request

# Roles relevant to platform-wide access. Other roles are passed through and
# interpreted by each service's own ROLE_PERMISSIONS mapping.
ROLE_PERMISSIONS = {
    "PLATFORM_ADMIN": {"*"},
    "ISP_OWNER": {"*"},
    "ISP_ADMIN": {"*"},
    "READ_ONLY": set(),
}


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def platform_jwt_secret() -> str:
    """Login signs with PLATFORM_JWT_SECRET (falls back to IDENTITY_JWT_SECRET).
    Services verify management JWTs with their own <SVC>_JWT_SECRET; set them all
    to the same value so one login token works across the platform."""
    return getenv("PLATFORM_JWT_SECRET") or getenv("IDENTITY_JWT_SECRET", "")


def issue_access_token(user_id, username: str, role: str, tenant_id=None,
                       expires_in_seconds: int | None = None) -> str:
    secret = platform_jwt_secret()
    if not secret or len(secret) < 32:
        raise HTTPException(503, "management authentication is not securely configured")
    ttl = int(getenv("IDENTITY_TOKEN_TTL_SECONDS", "43200"))
    now = int(time.time())
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


def decode_token(token: str) -> dict:
    secret = platform_jwt_secret()
    if not secret or not token:
        raise HTTPException(401, "missing bearer token")
    try:
        return jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401, "invalid or expired token")


def bearer_claims(request: Request) -> dict:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    return decode_token(header[7:])


async def internal_service_auth(request: Request) -> None:
    supplied = request.headers.get("X-Identity-Service-Key", "")
    configured = getenv("IDENTITY_INTERNAL_API_KEYS", getenv("IDENTITY_INTERNAL_API_KEY", ""))
    expected = [v.strip() for v in configured.split(",") if v.strip()]
    if not expected or not any(__import__("secrets").compare_digest(item, supplied) for item in expected):
        raise HTTPException(401, "internal service authentication failed")
