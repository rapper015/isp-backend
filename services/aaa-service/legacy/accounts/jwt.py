import re
import time

import jwt
from django.conf import settings

_DURATION_PATTERN = re.compile(r"^(\d+)\s*(d|h|m|s)?$", re.I)
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def _parse_expires_in_seconds(value: str) -> int:
    match = _DURATION_PATTERN.match(str(value).strip())
    if not match:
        return 86400
    amount = int(match.group(1))
    unit = (match.group(2) or "s").lower()
    return amount * _UNIT_SECONDS[unit]


def sign_admin_token(payload: dict) -> str:
    now = int(time.time())
    expires_in = _parse_expires_in_seconds(getattr(settings, "JWT_EXPIRES_IN", "1d"))
    claims = {**payload, "iat": now, "exp": now + expires_in}
    return jwt.encode(claims, settings.JWT_SECRET, algorithm="HS256")


def decode_admin_token(token: str) -> dict:
    return jwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"])
