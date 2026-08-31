"""Best-effort Redis/Valkey helpers for the OSS service. Every failed cache
operation fails open; Redis is never authoritative for reservations or order
state (the database is)."""
from __future__ import annotations

import json
import os
from typing import Any

import redis

PREFIX = "oss:v1"


def _client() -> redis.Redis:
    return redis.Redis.from_url(
        os.getenv("REDIS_URL", os.getenv("VALKEY_URL", "redis://127.0.0.1:6379/0")),
        socket_connect_timeout=0.15,
        socket_timeout=0.15,
        decode_responses=True,
    )


def get_json(cache_key: str) -> Any | None:
    try:
        value = _client().get(f"{PREFIX}:{cache_key}")
        return json.loads(value) if value else None
    except (redis.RedisError, json.JSONDecodeError):
        return None


def set_json(cache_key: str, value: Any, ttl: int | None = 60) -> None:
    try:
        _client().set(f"{PREFIX}:{cache_key}", json.dumps(value, default=str), ex=ttl)
    except redis.RedisError:
        pass


def delete_key(cache_key: str) -> None:
    try:
        _client().delete(f"{PREFIX}:{cache_key}")
    except redis.RedisError:
        pass


def delete_pattern(kind: str) -> None:
    try:
        connection = _client()
        for item in connection.scan_iter(f"{PREFIX}:{kind}:*", count=100):
            connection.delete(item)
    except redis.RedisError:
        pass


def limited(scope: str, maximum: int, window_seconds: int) -> bool:
    """True means allowed. Cannot become a security bypass when Redis is down."""
    try:
        connection = _client()
        count = connection.incr(f"{PREFIX}:limit:{scope}")
        if count == 1:
            connection.expire(f"{PREFIX}:limit:{scope}", window_seconds)
        return count <= maximum
    except redis.RedisError:
        return True


def acquire_lock(name: str, ttl: int = 30) -> bool:
    """Best-effort advisory lock; DB constraints remain authoritative."""
    try:
        return bool(_client().set(f"{PREFIX}:lock:{name}", "1", nx=True, ex=ttl))
    except redis.RedisError:
        return True


def release_lock(name: str) -> None:
    try:
        _client().delete(f"{PREFIX}:lock:{name}")
    except redis.RedisError:
        pass

