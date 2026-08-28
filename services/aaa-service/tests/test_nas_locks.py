"""Unit tests: per-NAS locks (database fallback) and circuit breaker."""
from uuid import uuid4

from app.circuit_breaker import STATE_CLOSED, STATE_HALF_OPEN, STATE_OPEN, allow_request, circuit_state, record_failure, record_success
from app.locks import acquire_nas_lock, release_nas_lock
from app.database import SessionLocal, engine, Base


def _fresh_session():
    Base.metadata.create_all(bind=engine)
    return SessionLocal()


def test_db_lock_blocks_second_owner_and_releases():
    session = _fresh_session()
    try:
        nas_id = uuid4()
        acquired, owner = acquire_nas_lock(session, nas_id, ttl_seconds=30)
        assert acquired is True
        second, _ = acquire_nas_lock(session, nas_id, owner="other-worker", ttl_seconds=30)
        assert second is False
        # Same owner re-enters.
        again, _ = acquire_nas_lock(session, nas_id, owner=owner, ttl_seconds=30)
        assert again is True
        release_nas_lock(session, nas_id, owner)
        third, _ = acquire_nas_lock(session, nas_id, owner="new-owner", ttl_seconds=30)
        assert third is True
    finally:
        session.close()


def test_db_lock_expiry_can_be_stealed():
    from datetime import datetime, timedelta, timezone
    from app.models import NasOperationLock
    session = _fresh_session()
    try:
        nas_id = uuid4()
        session.add(NasOperationLock(nas_id=nas_id, owner="stale", expires_at=datetime.now(timezone.utc) - timedelta(seconds=10)))
        session.commit()
        acquired, owner = acquire_nas_lock(session, nas_id, owner="new-owner", ttl_seconds=30)
        assert acquired is True
        assert owner == "new-owner"
    finally:
        session.close()


def test_circuit_breaker_opens_and_recovers():
    nas_id = uuid4()
    # Fresh state is closed.
    assert circuit_state(nas_id) == STATE_CLOSED
    assert allow_request(nas_id) is True
    opened = False
    for _ in range(3):
        if record_failure(nas_id, failure_threshold=3, reset_seconds=60):
            opened = True
    assert opened is True
    assert circuit_state(nas_id) == STATE_OPEN
    assert allow_request(nas_id) is False
    record_success(nas_id)
    assert circuit_state(nas_id) == STATE_CLOSED
    assert allow_request(nas_id) is True
