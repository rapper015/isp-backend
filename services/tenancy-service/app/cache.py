"""Tenant-aware cache helpers (Redis). All keys are prefixed with tenant scope
so one tenant can never read or poison another tenant's cache. Fail-open on
broker unavailability (callers treat cache as advisory)."""
from __future__ import annotations

import json
import time
from os import getenv

try:
    import redis as _redis

    _client = _redis.Redis.from_url(getenv("REDIS_URL", "redis://127.0.0.1:6379/0"),
                                    decode_responses=True)
except Exception:  # pragma: no cover - optional dependency
    _client = None

PREFIX = "ten:v1"


def _key(scope: str | None, key: str) -> str:
    if scope:
        return f"{PREFIX}:{scope}:{key}"
    return f"{PREFIX}:global:{key}"


def get_json(scope: str | None, key: str):
    if _client is None:
        return None
    try:
        raw = _client.get(_key(scope, key))
        return json.loads(raw) if raw else None
    except Exception:  # pragma: no cover
        return None


def set_json(scope: str | None, key: str, value, ttl: int = 300) -> None:
    if _client is None:
        return
    try:
        _client.set(_key(scope, key), json.dumps(value), ex=ttl)
    except Exception:  # pragma: no cover
        pass


def delete(scope: str | None, key: str) -> None:
    if _client is None:
        return
    try:
        _client.delete(_key(scope, key))
    except Exception:  # pragma: no cover
        pass


def limited(scope: str | None, key: str, limit: int, window: int = 60) -> bool:
    """Tenant-aware rate limit (sliding fixed window via INCR+EXPIRE)."""
    if _client is None:
        return True
    try:
        full = _key(scope, key)
        current = _client.incr(full)
        if current == 1:
            _client.expire(full, window)
        return current <= limit
    except Exception:  # pragma: no cover
        return True


def acquire_lock(scope: str | None, key: str, ttl: int = 30) -> bool:
    if _client is None:
        return True
    try:
        return bool(_client.set(_key(scope, key), "1", nx=True, ex=ttl))
    except Exception:  # pragma: no cover
        return True


def release_lock(scope: str | None, key: str) -> None:
    if _client is None:
        return
    try:
        _client.delete(_key(scope, key))
    except Exception:  # pragma: no cover
        pass


def _now_millis() -> int:
    return int(time.time() * 1000)
