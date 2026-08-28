"""Best-effort Valkey helpers. Every failed cache operation falls back to SQL."""
import json
from os import getenv
from typing import Any
import redis

PREFIX = "aaa:v1"
def key(tenant_id: str, kind: str, identity: str) -> str: return f"{PREFIX}:{tenant_id}:{kind}:{identity}"
def client():
    return redis.Redis.from_url(getenv("VALKEY_URL", "redis://127.0.0.1:6379/0"), socket_connect_timeout=0.15, socket_timeout=0.15, decode_responses=True)
def get_json(cache_key: str) -> dict[str, Any] | None:
    try:
        value = client().get(cache_key)
        return json.loads(value) if value else None
    except (redis.RedisError, json.JSONDecodeError): return None
def set_json(cache_key: str, value: dict[str, Any], ttl: int = 60) -> None:
    try: client().set(cache_key, json.dumps(value), ex=ttl)
    except redis.RedisError: pass
def delete_pattern(tenant_id: str, kind: str) -> None:
    try:
        connection = client()
        for item in connection.scan_iter(f"{PREFIX}:{tenant_id}:{kind}:*", count=100): connection.delete(item)
    except redis.RedisError: pass
def limited(scope: str, maximum: int, window_seconds: int) -> bool:
    """True means allowed. This cannot become a security bypass when Redis is down."""
    try:
        connection = client(); count = connection.incr(f"{PREFIX}:limit:{scope}")
        if count == 1: connection.expire(f"{PREFIX}:limit:{scope}", window_seconds)
        return count <= maximum
    except redis.RedisError: return True


# ---------------------------------------------------------------------------
# Milestone 3 — compiled-policy cache (Redis is an accelerator; the database
# remains authoritative for policies and decisions).
# ---------------------------------------------------------------------------

def cache_compiled_policy(tenant_id, subscriber_id, compiled: dict, ttl: int = 300) -> None:
    set_json(key(str(tenant_id), "policy", str(subscriber_id)), compiled, ttl=ttl)


def get_compiled_policy(tenant_id, subscriber_id) -> dict | None:
    return get_json(key(str(tenant_id), "policy", str(subscriber_id)))


def invalidate_compiled_policy(tenant_id, subscriber_id) -> None:
    delete_pattern(str(tenant_id), "policy")


def cache_control_throttle(tenant_id, nas_id, window_seconds: int = 1) -> bool:
    """Per-NAS control-action rate limit (fail-open)."""
    return limited(f"control:{tenant_id}:{nas_id}", 1, window_seconds)
