"""Staged shared-secret rotation between the manually hosted FreeRADIUS and
the router.

The MikroTik secret is never changed before FreeRADIUS is prepared unless the
administrator explicitly chooses a planned-outage workflow. The previous
encrypted secret is retained for a short rollback window.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Nas, NasRadiusAssignment, NasSecretReveal, NasSecretRotation
from .nas_lifecycle import secret_rotation_transition
from .nas_registration import REVEAL_TTL_SECONDS
from .routeros import RouterOSAdapter, redact
from .security import decrypt_secret, encrypt_secret, new_shared_secret

ROLLBACK_WINDOW_SECONDS = 3600


def start_secret_rotation(session: Session, nas: Nas, assignment: NasRadiusAssignment, created_by: str = "internal-radius") -> NasSecretRotation:
    """Generate a new assignment-specific secret and stage it as pending."""
    old_secret = decrypt_secret(assignment.secret_ciphertext)
    new_secret = new_shared_secret()
    new_version = assignment.secret_version + 1
    rotation = NasSecretRotation(
        nas_id=nas.id,
        assignment_id=assignment.id,
        state="ROTATION_DRAFT",
        old_secret_ciphertext=encrypt_secret(old_secret),
        old_secret_version=assignment.secret_version,
        new_secret_ciphertext=encrypt_secret(new_secret),
        new_secret_version=new_version,
        created_by=created_by,
    )
    session.add(rotation)
    session.flush()
    rotation.state = secret_rotation_transition(rotation.state, "NEW_SECRET_GENERATED")
    return rotation


def rotation_registration_package(session: Session, rotation: NasSecretRotation) -> dict:
    """Produce a one-time manual FreeRADIUS update package for the new secret."""
    rotation.state = secret_rotation_transition(rotation.state, "AWAITING_FREERADIUS_UPDATE")
    token = secrets.token_urlsafe(32)
    session.add(NasSecretReveal(
        assignment_id=rotation.assignment_id,
        rotation_id=rotation.id,
        secret_ciphertext=rotation.new_secret_ciphertext,
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=REVEAL_TTL_SECONDS),
    ))
    session.flush()
    return {"reveal_token": token, "expires_in_seconds": REVEAL_TTL_SECONDS, "secret_version": rotation.new_secret_version}


def reveal_rotation_secret(session: Session, rotation: NasSecretRotation, token: str) -> dict:
    """Reveal the pending new secret once for the FreeRADIUS administrator."""
    from sqlalchemy import select as _select
    session.flush()
    reveal = session.scalar(_select(NasSecretReveal).where(NasSecretReveal.rotation_id == rotation.id, NasSecretReveal.token_hash == hashlib.sha256(token.encode()).hexdigest()))
    if reveal is None or reveal.accessed_at:
        raise ValueError("rotation reveal token is invalid or expired")
    expires_at = reveal.expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at is not None and expires_at < datetime.now(timezone.utc):
        raise ValueError("rotation reveal token is invalid or expired")
    reveal.accessed_at = datetime.now(timezone.utc)
    return {"shared_secret": decrypt_secret(reveal.secret_ciphertext or rotation.new_secret_ciphertext), "secret_version": rotation.new_secret_version, "display_once": True}


def confirm_freeradius_update(session: Session, rotation: NasSecretRotation, actor: str = "internal-radius") -> dict:
    """Administrator confirms the new secret was applied in FreeRADIUS."""
    rotation.state = secret_rotation_transition(rotation.state, "FREERADIUS_UPDATE_CONFIRMED")
    rotation.freeradius_confirmations = {**rotation.freeradius_confirmations, actor: datetime.now(timezone.utc).isoformat()}
    return {"state": rotation.state}


def apply_secret_to_router(session: Session, rotation: NasSecretRotation, adapter: RouterOSAdapter) -> dict:
    """Update the MikroTik RADIUS entry with the pending secret."""
    rotation.state = secret_rotation_transition(rotation.state, "ROUTER_UPDATE_PENDING")
    assignment = session.get(NasRadiusAssignment, rotation.assignment_id)
    if assignment is None:
        raise ValueError("rotation assignment not found")
    remote_id = assignment.remote_object_id
    if not remote_id:
        # Fall back to locating the entry by server address.
        from .models import RadiusServer
        server = session.get(RadiusServer, assignment.radius_server_id)
        address = server.host if server else ""
        entries = adapter.get_radius_entries()
        matched = next((entry for entry in entries if entry.get("address") == address), None)
        if matched is None:
            raise ValueError("managed RADIUS entry not found for rotation")
        remote_id = matched.get("remote_id")
    adapter.update_radius_entry(remote_id, {"secret": decrypt_secret(rotation.new_secret_ciphertext)})
    rotation.state = secret_rotation_transition(rotation.state, "ROUTER_UPDATED")
    rotation.state = secret_rotation_transition(rotation.state, "VERIFYING")
    return {"state": rotation.state, "remote_object_id": remote_id}


def verify_rotation(session: Session, rotation: NasSecretRotation) -> dict:
    """Mark rotation active after a functional verification signal.

    The assignment secret is advanced to the new version and the previous
    secret is retained for the rollback window.
    """
    rotation.state = secret_rotation_transition(rotation.state, "ACTIVE")
    assignment = session.get(NasRadiusAssignment, rotation.assignment_id)
    if assignment is not None:
        assignment.secret_ciphertext = rotation.new_secret_ciphertext
        assignment.secret_version = rotation.new_secret_version
        assignment.applied_status = "applied"
    rotation.completed_at = datetime.now(timezone.utc)
    rotation.rollback_available_until = datetime.now(timezone.utc) + timedelta(seconds=ROLLBACK_WINDOW_SECONDS)
    return {"state": rotation.state, "secret_version": rotation.new_secret_version, "rollback_available_until": rotation.rollback_available_until.isoformat()}


def rollback_secret(session: Session, rotation: NasSecretRotation, adapter: RouterOSAdapter | None = None) -> dict:
    """Restore the previous secret on the router and the assignment record."""
    rotation.state = secret_rotation_transition(rotation.state, "ROLLBACK_PENDING")
    assignment = session.get(NasRadiusAssignment, rotation.assignment_id)
    if adapter is not None and assignment is not None:
        remote_id = assignment.remote_object_id
        if not remote_id:
            from .models import RadiusServer
            server = session.get(RadiusServer, assignment.radius_server_id)
            address = server.host if server else ""
            entries = adapter.get_radius_entries()
            matched = next((entry for entry in entries if entry.get("address") == address), None)
            if matched is None:
                raise ValueError("managed RADIUS entry not found for rollback")
            remote_id = matched.get("remote_id")
        old_secret = decrypt_secret(rotation.old_secret_ciphertext or "")
        adapter.update_radius_entry(remote_id, {"secret": old_secret})
    if assignment is not None and rotation.old_secret_ciphertext:
        assignment.secret_ciphertext = rotation.old_secret_ciphertext
        assignment.secret_version = rotation.old_secret_version or 1
    rotation.state = secret_rotation_transition(rotation.state, "ROLLED_BACK")
    return {"state": rotation.state}


def expire_old_secret(session: Session, rotation: NasSecretRotation) -> dict:
    """Archive/clear the previous secret after the rollback window (policy)."""
    if rotation.state != "ACTIVE":
        raise ValueError("only active rotations may expire their old secret")
    rotation.old_secret_ciphertext = None
    rotation.old_secret_version = None
    return {"state": rotation.state, "old_secret_retained": False}
