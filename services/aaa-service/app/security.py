import hashlib
import secrets
from os import getenv
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

async def internal_service_auth(request: Request) -> None:
    supplied = request.headers.get("X-AAA-Service-Key", "")
    configured = getenv("AAA_INTERNAL_API_KEYS", getenv("AAA_INTERNAL_API_KEY", ""))
    expected = [value.strip() for value in configured.split(",") if value.strip()]
    if not expected or not any(secrets.compare_digest(item, supplied) for item in expected): raise HTTPException(401, "internal service authentication failed")
    trusted = [value.strip() for value in getenv("AAA_TRUSTED_SOURCES", "").split(",") if value.strip()]
    if trusted and (not request.client or request.client.host not in trusted): raise HTTPException(403, "untrusted source")
    mtls_identities = [value.strip() for value in getenv("AAA_MTLS_IDENTITIES", "").split(",") if value.strip()]
    if mtls_identities and request.headers.get("X-Client-Certificate-Identity", "") not in mtls_identities: raise HTTPException(401, "mTLS identity not allowed")
    remote = request.client.host if request.client else "unknown"
    if not limited(f"internal:{remote}:{request.url.path}", int(getenv("AAA_INTERNAL_RATE_LIMIT", "300")), 60): raise HTTPException(429, "rate limit exceeded")
