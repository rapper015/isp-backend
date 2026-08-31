"""BSS security: Fernet encryption for gateway credentials and webhook secrets."""
from os import getenv

from cryptography.fernet import Fernet


def _key() -> bytes:
    value = getenv("BSS_ENCRYPTION_KEY", "")
    if not value:
        raise RuntimeError("BSS_ENCRYPTION_KEY must be configured")
    return value.encode()


def encrypt_secret(value: str) -> str:
    return Fernet(_key()).encrypt(value.encode()).decode()


def decrypt_secret(value: str) -> str:
    return Fernet(_key()).decrypt(value.encode()).decode()
