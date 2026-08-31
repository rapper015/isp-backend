"""SIEM crypto helpers: at-rest field encryption (417), PII masking (418),
and tamper-evident evidence hashing (408)."""
import hashlib
import hmac
import json
import os
import re

from cryptography.fernet import Fernet, InvalidToken


def _fernet() -> Fernet:
    key = os.getenv("SIEM_ENCRYPTION_KEY")
    if not key:
        raise RuntimeError("SIEM_ENCRYPTION_KEY is not configured")
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except (ValueError, TypeError):
        # allow plain >=32 char secrets by deriving a Fernet key
        digest = hashlib.sha256(key.encode()).digest()
        return Fernet(__import__("base64").urlsafe_b64encode(digest))


def encrypt_field(value: str | None) -> str | None:
    """Encrypt a sensitive value (AES-128-CBC via Fernet) for storage."""
    if value is None:
        return None
    return _fernet().encrypt(value.encode()).decode()


def decrypt_field(token: str | None) -> str | None:
    if not token:
        return None
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        return None


def mask_pii(value: str | None, kind: str = "auto") -> str | None:
    """Mask PII (email/phone/IP) for display (feature 418 Data Masking)."""
    if value is None:
        return None
    if kind == "email" or "@" in value:
        local, _, domain = value.partition("@")
        if domain:
            return f"{local[:2]}{'*' * max(0, len(local) - 2)}@{domain}"
    if kind == "phone" or re.fullmatch(r"\+?[\d\s()-]{7,}", value or ""):
        digits = re.sub(r"\D", "", value)
        if len(digits) >= 8:
            return f"+{'*' * max(0, len(digits) - 4)}{digits[-4:]}"
    if kind == "ip" or re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", value or ""):
        return ".".join(["*", "*", "*", value.split(".")[-1]])
    return value[:2] + "*" * max(0, len(value) - 2) if len(value) > 4 else "*" * len(value)


def sha256(payload: dict | str) -> str:
    if not isinstance(payload, str):
        payload = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def hash_chain(prev_hash: str | None, payload: dict) -> str:
    """Compute an evidence digest chained to the previous block (feature 408)."""
    data = (prev_hash or "") + "|" + json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(data.encode()).hexdigest()


def chain_hash(prev_hash: str | None, canonical: str) -> str:
    """Chain digest over a canonical serialized payload string."""
    return hashlib.sha256(((prev_hash or "") + "|" + canonical).encode()).hexdigest()


def verify_chain(prev_hash: str | None, payload: dict, expected: str) -> bool:
    return hmac.compare_digest(hash_chain(prev_hash, payload), expected)
