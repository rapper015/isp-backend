"""Secret handling: encrypted storage references, masking, and redaction.

Raw secrets (CWMP/PPPoE/Wi-Fi/connection-request passwords, ACS API secrets)
are never stored in plaintext, never logged, and never returned by APIs. In
tests a deterministic reversible cipher keyed by an env secret is used so the
flow is verifiable without a live KMS; production should swap this for a real
KMS/vault-backed reference."""
from __future__ import annotations

import base64
import hashlib
import os
from cryptography.fernet import Fernet

_SECRET = os.getenv("DEVICE_MANAGEMENT_ENCRYPTION_KEY", "dev-only-key-not-for-production-0123456789ab")


def _derive_key(secret: str) -> bytes:
    digest = hashlib.sha256(secret.encode()).digest()
    return base64.urlsafe_b64encode(digest)


def _fernet():
    return Fernet(_derive_key(_SECRET))


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(secret_ref: str) -> str:
    return _fernet().decrypt(secret_ref.encode()).decode()


def mask_secret(plaintext: str | None, *, keep_tail: int = 4) -> str:
    if not plaintext:
        return "••••"
    visible = plaintext[-keep_tail:] if len(plaintext) > keep_tail else "****"
    return "••••" + visible


def redact_log_line(line: str) -> str:
    """Best-effort redaction of common secret-looking values in log strings."""
    import re

    return re.sub(r'(password\s*[:=]?\s*["\']?)([^"\'\s,}]+)', r'\1••••', line, flags=re.IGNORECASE)
