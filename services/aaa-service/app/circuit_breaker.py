"""Per-NAS circuit breaker.

State is kept in Redis (short-lived). When Redis is unavailable an in-memory
per-process fallback is used; it never makes a healthy router look broken
across workers, it only trips after repeated failures in this process.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import Lock
import redis

from .cache import PREFIX, client

_fallback: dict[str, dict] = {}
_fallback_lock = Lock()

STATE_CLOSED = "CLOSED"
STATE_OPEN = "OPEN"
STATE_HALF_OPEN = "HALF_OPEN"


def _key(nas_id, suffix: str) -> str:
    return f"{PREFIX}:cb:{nas_id}:{suffix}"


def circuit_state(nas_id) -> str:
    """Return CLOSED, OPEN or HALF_OPEN for a NAS."""
    try:
        connection = client()
        opened_until = connection.get(_key(nas_id, "open_until"))
        if opened_until:
            if float(opened_until) > datetime.now(timezone.utc).timestamp():
                return STATE_OPEN
            return STATE_HALF_OPEN
        return STATE_CLOSED
    except redis.RedisError:
        with _fallback_lock:
            record = _fallback.get(str(nas_id))
            if not record:
                return STATE_CLOSED
            if record["failures"] >= int(record.get("threshold", 3)):
                if record["open_until"] and record["open_until"] > datetime.now(timezone.utc):
                    return STATE_OPEN
                return STATE_HALF_OPEN
            return STATE_CLOSED


def allow_request(nas_id, failure_threshold: int = 3, reset_seconds: int = 300) -> bool:
    state = circuit_state(nas_id)
    if state == STATE_OPEN:
        return False
    # In HALF_OPEN exactly one probe is permitted per NAS per reset window.
    if state == STATE_HALF_OPEN:
        try:
            connection = client()
            return bool(connection.set(_key(nas_id, "probe"), "1", nx=True, ex=reset_seconds))
        except redis.RedisError:
            with _fallback_lock:
                record = _fallback.setdefault(str(nas_id), {"failures": 0, "open_until": None, "threshold": failure_threshold})
                record["open_until"] = None
                return record.get("last_probe") != datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
    return True


def record_success(nas_id) -> None:
    try:
        connection = client()
        connection.delete(_key(nas_id, "failures"), _key(nas_id, "open_until"), _key(nas_id, "probe"))
    except redis.RedisError:
        with _fallback_lock:
            _fallback.pop(str(nas_id), None)


def record_failure(nas_id, failure_threshold: int = 3, reset_seconds: int = 300) -> bool:
    """Record a failure and return True when the circuit just opened."""
    try:
        connection = client()
        count = connection.incr(_key(nas_id, "failures"))
        if count == 1:
            connection.expire(_key(nas_id, "failures"), reset_seconds)
        if count >= failure_threshold:
            connection.set(_key(nas_id, "open_until"), datetime.now(timezone.utc).timestamp() + reset_seconds, ex=reset_seconds)
            return True
        return False
    except redis.RedisError:
        with _fallback_lock:
            record = _fallback.setdefault(str(nas_id), {"failures": 0, "open_until": None, "threshold": failure_threshold, "last_probe": None})
            record["failures"] = record.get("failures", 0) + 1
            if record["failures"] >= failure_threshold:
                record["open_until"] = datetime.now(timezone.utc) + timedelta(seconds=reset_seconds)
                return True
            return False
