"""Best-effort Redis helpers (fail-open; never the source of truth)."""
import json
from os import getenv
from typing import Any

import redis

PREFIX = "bss:v1"


def client():
    return redis.Redis.from_url(getenv("VALKEY_URL", "redis://127.0.0.1:6379/0"), socket_connect_timeout=0.15, socket_timeout=0.15, decode_responses=True)


def get_json(key: str) -> dict[str, Any] | None:
    try:
        value = client().get(f"{PREFIX}:{key}")
        return json.loads(value) if value else None
    except (redis.RedisError, json.JSONDecodeError):
        return None


def set_json(key: str, value: dict[str, Any], ttl: int = 60) -> None:
    try:
        client().set(f"{PREFIX}:{key}", json.dumps(value, default=str), ex=ttl)
    except redis.RedisError:
        pass


def delete_pattern(tenant_id: str, kind: str) -> None:
    try:
        connection = client()
        for item in connection.scan_iter(f"{PREFIX}:{tenant_id}:{kind}:*", count=100):
            connection.delete(item)
    except redis.RedisError:
        pass


def limited(scope: str, maximum: int, window_seconds: int) -> bool:
    try:
        connection = client()
        count = connection.incr(f"{PREFIX}:limit:{scope}")
        if count == 1:
            connection.expire(f"{PREFIX}:limit:{scope}", window_seconds)
        return count <= maximum
    except redis.RedisError:
        return True
