"""CRM value validation and normalization. Phone/email normalization is applied
before uniqueness checks to avoid duplicate identities."""
from __future__ import annotations

import re

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_RE = re.compile(r"^\+?[0-9][0-9\s\-()]{5,19}$")


class ValidationError(ValueError):
    pass


def normalize_phone(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    digits = re.sub(r"[^0-9]", "", value)
    if len(digits) < 7 or len(digits) > 15:
        raise ValidationError("invalid phone number")
    # The current product is India-first and older records were stored as
    # national 10-digit values. Canonicalize both national and +91 forms to
    # the same E.164 digits so formatting/country-prefix changes cannot evade
    # uniqueness checks.
    if len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 10:
        digits = f"91{digits}"
    return digits


def normalize_email(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    email = value.strip().lower()
    if not _EMAIL_RE.fullmatch(email) or len(email) > 255:
        raise ValidationError("invalid email address")
    return email


def validate_zipcode(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    value = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9\- ]{3,16}", value):
        raise ValidationError("invalid postal code")
    return value


def validate_coordinates(latitude: float | None, longitude: float | None) -> None:
    if latitude is not None and not (-90 <= latitude <= 90):
        raise ValidationError("invalid latitude")
    if longitude is not None and not (-180 <= longitude <= 180):
        raise ValidationError("invalid longitude")


def mask_identifier(value: str | None) -> str | None:
    """Return a masked identifier (e.g., last four characters only)."""
    if value is None:
        return None
    value = value.strip()
    if len(value) <= 4:
        return "*" * len(value)
    return "*" * (len(value) - 4) + value[-4:]
