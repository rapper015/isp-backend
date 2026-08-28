"""Per-NAS distributed locks with a database fallback.

Redis is the primary lock store. When Redis is unavailable the same semantics
are enforced through the ``nas_operation_locks`` table so two workers can
never configure the same NAS simultaneously.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4
import redis
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .cache import PREFIX, client
from .models import NasOperationLock


class NasLockError(Exception):
    """Raised when a NAS lock cannot be acquired after retries."""


def _redis_key(nas_id) -> str:
    return f"{PREFIX}:lock:{nas_id}"


def acquire_nas_lock(session: Session, nas_id, owner: str | None = None, ttl_seconds: int = 90) -> tuple[bool, str]:
    """Acquire the per-NAS lock. Returns (acquired, owner)."""
    owner = owner or uuid4().hex
    if _redis_acquire(nas_id, owner, ttl_seconds):
        return True, owner
    return _db_acquire(session, nas_id, owner, ttl_seconds)


def _redis_acquire(nas_id, owner: str, ttl_seconds: int) -> bool:
    try:
        return bool(client().set(_redis_key(nas_id), owner, nx=True, ex=ttl_seconds))
    except redis.RedisError:
        return False


def _db_acquire(session: Session, nas_id, owner: str, ttl_seconds: int) -> tuple[bool, str]:
    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=ttl_seconds)
    lock = session.get(NasOperationLock, nas_id)
    try:
        if lock is None:
            session.add(NasOperationLock(nas_id=nas_id, owner=owner, expires_at=expires))
            session.commit()
            return True, owner
        if lock.owner == owner:
            lock.expires_at = expires
            session.commit()
            return True, owner
        expires_at = lock.expires_at
        if expires_at is not None and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at is not None and expires_at < now:
            result = session.execute(
                update(NasOperationLock)
                .where(NasOperationLock.nas_id == nas_id, NasOperationLock.expires_at < now)
                .values(owner=owner, expires_at=expires)
                .execution_options(synchronize_session=False)
            )
            session.commit()
            return result.rowcount == 1, owner
        return False, lock.owner
    except IntegrityError:
        session.rollback()
        return False, owner


def release_nas_lock(session: Session, nas_id, owner: str) -> None:
    _redis_release(nas_id, owner)
    _db_release(session, nas_id, owner)


def _redis_release(nas_id, owner: str) -> None:
    try:
        connection = client()
        current = connection.get(_redis_key(nas_id))
        if current == owner:
            connection.delete(_redis_key(nas_id))
    except redis.RedisError:
        pass


def _db_release(session: Session, nas_id, owner: str) -> None:
    lock = session.get(NasOperationLock, nas_id)
    if lock is not None and lock.owner == owner:
        session.delete(lock)
        session.commit()
