"""Redis-backed cache, rate limiting and short-lived locks. All helpers
fail open — Redis is never the source of truth for business state."""
from os import getenv
from time import monotonic

PREFIX = "device:v1"

try:
    import redis

    _client = redis.Redis.from_url(getenv("REDIS_URL", "redis://localhost:6379/0"),
                                   socket_connect_timeout=0.5, socket_timeout=0.5)
except Exception:  # noqa: BLE001 — optional dependency
    _client = None


def _key(name: str) -> str:
    return f"{PREFIX}:{name}"


def get_json(name: str):
    if _client is None:
        return None
    try:
        value = _client.get(_key(name))
        return value.decode() if isinstance(value, bytes) else value
    except Exception:  # noqa: BLE001
        return None


def set_json(name: str, value: str, ttl: int = 60) -> None:
    if _client is None:
        return
    try:
        _client.set(_key(name), value, ex=ttl)
    except Exception:  # noqa: BLE001
        pass


def limited(name: str, limit: int, window_seconds: int) -> bool:
    """Sliding-window rate limit. Returns True when the call is allowed."""
    if _client is None:
        return True
    try:
        now = monotonic()
        bucket = _key(name)
        pipe = _client.pipeline()
        pipe.zremrangebyscore(bucket, 0, now - window_seconds)
        pipe.zadd(bucket, {str(now): now})
        pipe.zcard(bucket)
        pipe.expire(bucket, window_seconds + 1)
        results = pipe.execute()
        return int(results[-2] or 0) <= limit
    except Exception:  # noqa: BLE001
        return True


def acquire_lock(name: str, ttl_seconds: int = 30) -> bool:
    """Try to acquire a short-lived lock. Fails open on Redis errors."""
    if _client is None:
        return True
    try:
        return bool(_client.set(_key(f"lock:{name}"), "1", nx=True, ex=ttl_seconds))
    except Exception:  # noqa: BLE001
        return True


def release_lock(name: str) -> None:
    if _client is None:
        return
    try:
        _client.delete(_key(f"lock:{name}"))
    except Exception:  # noqa: BLE001
        pass
