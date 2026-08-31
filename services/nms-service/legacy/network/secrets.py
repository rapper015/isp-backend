from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def _fernet():
    if not settings.NAS_ENCRYPTION_KEY:
        raise ImproperlyConfigured("NAS_ENCRYPTION_KEY must be configured")
    try:
        return Fernet(settings.NAS_ENCRYPTION_KEY.encode())
    except (ValueError, TypeError) as exc:
        raise ImproperlyConfigured("NAS_ENCRYPTION_KEY is not a valid Fernet key") from exc


def encrypt_secret(value):
    return _fernet().encrypt(value.encode()).decode() if value else ""


def decrypt_secret(value):
    if not value: return ""
    try: return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as exc: raise ImproperlyConfigured("Stored NAS secret cannot be decrypted") from exc


def redact(value):
    sensitive=("password","secret","token","credential")
    if isinstance(value,dict): return {k:("[REDACTED]" if any(x in k.casefold() for x in sensitive) else redact(v)) for k,v in value.items()}
    if isinstance(value,list): return [redact(item) for item in value]
    return value
