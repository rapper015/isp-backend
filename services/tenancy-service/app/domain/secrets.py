"""Secret handling: encrypted references, masking and log redaction.

Sensitive partner/tenant credentials (bank refs, integration secrets, API
credential secrets, database credentials) are stored only as encrypted
references and never returned by APIs or logged."""
from __future__ import annotations

import base64
import hashlib
import os
import re

from cryptography.fernet import Fernet

_SECRET = os.getenv("TENANCY_ENCRYPTION_KEY", "dev-only-key-not-for-production-0123456789ab")


def _derive_key(secret: str) -> bytes:
    digest = hashlib.sha256(secret.encode()).digest()
    return base64.urlsafe_b64encode(digest)


def _fernet() -> Fernet:
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
    return re.sub(r'(password|secret|token|key|bank_ref)\s*[:=]?\s*["\']?([^"\'\s,}]+)',
                  r'\1=••••', line, flags=re.IGNORECASE)
