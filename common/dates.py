from django.utils import timezone
from django.utils.dateparse import parse_datetime


def parse_dt(value):
    """Parse an ISO datetime string (or pass through a datetime) to an
    aware datetime, defaulting to UTC when no offset is given. Returns
    None if value is missing/blank/unparseable.
    """
    if value in (None, ""):
        return None

    if isinstance(value, str):
        parsed = parse_datetime(value)
        if parsed is None:
            return None
    else:
        parsed = value

    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.UTC)
    return parsed


def require_dt(payload: dict, field: str):
    from aaa.exceptions import AppError

    value = parse_dt(payload.get(field))
    if value is None:
        raise AppError("Validation failed", 400, {"missingFields": [field]})
    return value
