"""Integration tests: staged shared-secret rotation workflow.

FreeRADIUS is manual, so rotation is staged: generate -> await FreeRADIUS
update -> confirm -> apply to router -> verify -> active, with rollback.
"""
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.database import Base, SessionLocal, engine
from app.models import Nas, NasRadiusAssignment, NasSecretRotation, NasSecretReveal, RadiusServer, Tenant
from app.nas_rotation import (apply_secret_to_router, confirm_freeradius_update, expire_old_secret, reveal_rotation_secret, rollback_secret, rotation_registration_package, start_secret_rotation, verify_rotation)
from app.routeros import FakeRouterOSAdapter
from app.security import decrypt_secret, encrypt_secret, new_shared_secret


@pytest.fixture
def session():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    yield db
    db.close()


def _setup(session):
    tenant = Tenant(name=f"rot-{uuid4().hex}")
    session.add(tenant)
    session.flush()
    nas = Nas(tenant_id=tenant.id, name="router", source_ip="10.50.5.2", management_host="10.50.5.2", lifecycle_status="ACTIVE")
    session.add(nas)
    session.flush()
    server = RadiusServer(name=f"radius-{uuid4().hex}", host="192.0.2.91", api_key_hash="hash", enabled=True)
    session.add(server)
    session.flush()
    secret = new_shared_secret()
    assignment = NasRadiusAssignment(nas_id=nas.id, radius_server_id=server.id, priority=100, role="primary", services=["pppoe"], secret_ciphertext=encrypt_secret(secret), secret_version=1, registration_status="VERIFIED", remote_object_id="*1")
    session.add(assignment)
    session.flush()
    return nas, assignment, secret


def test_full_staged_rotation_workflow(session):
    nas, assignment, old_secret = _setup(session)
    adapter = FakeRouterOSAdapter()
    adapter.create_radius_entry({"address": "192.0.2.91", "secret": old_secret, "service": ["pppoe"], "accounting_port": 1813, "timeout": 3000})

    # 1. Start rotation: new secret generated and staged.
    rotation = start_secret_rotation(session, nas, assignment)
    assert rotation.state == "NEW_SECRET_GENERATED"
    assert rotation.new_secret_version == 2
    assert rotation.old_secret_version == 1
    assert decrypt_secret(rotation.old_secret_ciphertext) == old_secret
    assert decrypt_secret(rotation.new_secret_ciphertext) != old_secret

    # 2. Produce a one-time FreeRADIUS update package.
    package = rotation_registration_package(session, rotation)
    assert rotation.state == "AWAITING_FREERADIUS_UPDATE"
    reveal = reveal_rotation_secret(session, rotation, package["reveal_token"])
    assert reveal["secret_version"] == 2
    assert reveal["shared_secret"] == decrypt_secret(rotation.new_secret_ciphertext)
    with pytest.raises(ValueError):
        reveal_rotation_secret(session, rotation, package["reveal_token"])  # once only

    # 3. Wait for admin confirmation.
    confirm_freeradius_update(session, rotation)
    assert rotation.state == "FREERADIUS_UPDATE_CONFIRMED"

    # 4. Apply to the router (MikroTik side).
    result = apply_secret_to_router(session, rotation, adapter)
    assert result["state"] == "VERIFYING"
    assert adapter.get_radius_entries()[0]["address"] == "192.0.2.91"

    # 5. Verify -> active; assignment secret advances.
    verify_rotation(session, rotation)
    assert rotation.state == "ACTIVE"
    session.flush()
    session.expire_all()
    fresh_assignment = session.get(NasRadiusAssignment, assignment.id)
    assert fresh_assignment.secret_version == 2
    assert decrypt_secret(fresh_assignment.secret_ciphertext) == decrypt_secret(rotation.new_secret_ciphertext)
    assert rotation.rollback_available_until is not None

    # 6. Old secret retained for the rollback window, then expired by policy.
    expire_old_secret(session, rotation)
    assert rotation.old_secret_ciphertext is None


def test_rotation_rollback_restores_old_secret(session):
    nas, assignment, old_secret = _setup(session)
    adapter = FakeRouterOSAdapter()
    adapter.create_radius_entry({"address": "192.0.2.91", "secret": old_secret, "service": ["pppoe"], "accounting_port": 1813, "timeout": 3000})
    rotation = start_secret_rotation(session, nas, assignment)
    rotation_registration_package(session, rotation)
    confirm_freeradius_update(session, rotation)
    apply_secret_to_router(session, rotation, adapter)
    # Rollback before verification.
    result = rollback_secret(session, rotation, adapter)
    assert result["state"] == "ROLLED_BACK"
    session.refresh(assignment)
    assert assignment.secret_version == 1
    assert decrypt_secret(assignment.secret_ciphertext) == old_secret


def test_rotation_rejects_invalid_transition(session):
    nas, assignment, _ = _setup(session)
    rotation = start_secret_rotation(session, nas, assignment)
    with pytest.raises(ValueError):
        verify_rotation(session, rotation)  # NEW_SECRET_GENERATED -> ACTIVE invalid

