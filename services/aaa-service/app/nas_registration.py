"""Manual FreeRADIUS registration tracking and secure registration packages.

FreeRADIUS is hosted outside this service. The backend generates the details an
administrator must configure manually, tracks manual confirmation separately
from technical verification, and never writes or restarts FreeRADIUS.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from .models import Nas, NasRadiusAssignment, NasSecretReveal, RadiusServer
from .nas_lifecycle import registration_transition
from .security import decrypt_secret

REVEAL_TTL_SECONDS = 600

# Signals that constitute technical verification of FreeRADIUS registration.
VERIFICATION_SIGNALS = {"authentication_request_observed", "accounting_request_observed", "integration_test_result", "freeradius_callback"}


def generate_registration_package(session: Session, nas: Nas, assignment: NasRadiusAssignment, reveal_ttl_seconds: int = REVEAL_TTL_SECONDS) -> dict:
    """Issue a one-time reveal token for the assignment's shared secret.

    The token itself is returned once; the secret is only returned by the
    dedicated reveal operation. Registration state moves through
    DETAILS_GENERATED -> AWAITING_MANUAL_CONFIGURATION.
    """
    current = assignment.registration_status or "PENDING"
    if current in {"NOT_REQUIRED", "DISABLED"}:
        raise ValueError("registration is not required or is disabled")
    # Re-issuing a package is idempotent: move forward through the machine when
    # possible, otherwise stay in the awaiting state.
    if current == "PENDING":
        assignment.registration_status = registration_transition(current, "DETAILS_GENERATED")
        assignment.registration_status = registration_transition(assignment.registration_status, "AWAITING_MANUAL_CONFIGURATION")
    elif current == "DETAILS_GENERATED":
        assignment.registration_status = registration_transition(current, "AWAITING_MANUAL_CONFIGURATION")
    elif current == "AWAITING_MANUAL_CONFIGURATION":
        pass
    else:
        assignment.registration_status = "AWAITING_MANUAL_CONFIGURATION"
    token = secrets.token_urlsafe(32)
    session.add(NasSecretReveal(
        assignment_id=assignment.id,
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=reveal_ttl_seconds),
    ))
    session.flush()
    return {"reveal_token": token, "expires_in_seconds": reveal_ttl_seconds}


def reveal_registration_package(session: Session, nas: Nas, assignment: NasRadiusAssignment, token: str) -> dict:
    """Return the full registration package including the shared secret.

    The token is single-use and expires. Access is audited by the caller.
    """
    from sqlalchemy import select
    session.flush()
    reveal = session.scalar(
        select(NasSecretReveal).where(
            NasSecretReveal.token_hash == hashlib.sha256(token.encode()).hexdigest(),
            NasSecretReveal.assignment_id == assignment.id,
        )
    )
    expires_at = reveal.expires_at if reveal else None
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if reveal is None or reveal.accessed_at or (expires_at is not None and expires_at < datetime.now(timezone.utc)):
        raise ValueError("reveal token is invalid or expired")
    reveal.accessed_at = datetime.now(timezone.utc)
    server = session.get(RadiusServer, assignment.radius_server_id)
    secret = decrypt_secret(assignment.secret_ciphertext)
    message_authenticator = "yes" if (nas.capabilities or {}).get("message_authenticator_options", False) else "no"
    generated_at = datetime.now(timezone.utc)
    return {
        "nas_name": nas.name,
        "nas_short_name": nas.short_name or nas.name[:64],
        "nas_source_ip": nas.source_ip,
        "nas_source_cidr": nas.source_cidr or f"{nas.source_ip}/32",
        "nas_identifier": nas.nas_identifier,
        "vendor": nas.vendor,
        "radius_server": server.host if server else None,
        "authentication_port": assignment.auth_port or (server.auth_port if server else 1812),
        "accounting_port": assignment.accounting_port or (server.accounting_port if server else 1813),
        "coa_port": assignment.coa_port or (server.coa_port if server else 3799),
        "services": assignment.services,
        "message_authenticator_required": message_authenticator,
        "shared_secret": secret,
        "secret_version": assignment.secret_version,
        "generated_at": generated_at.isoformat(),
        "expires_at": reveal.expires_at.isoformat() if reveal.expires_at else generated_at.isoformat(),
        "display_once": True,
    }


def confirm_manual_registration(session: Session, nas: Nas, assignment: NasRadiusAssignment, details: dict | None = None) -> dict:
    """Record that an administrator manually configured FreeRADIUS.

    Manual confirmation alone never marks the registration verified; technical
    verification requires a functional signal (recorded separately).
    """
    current = assignment.registration_status or "PENDING"
    if current in {"NOT_REQUIRED", "DISABLED", "VERIFIED"}:
        raise ValueError(f"registration cannot be confirmed from {current}")
    # Confirmation is valid from the generated/awaiting states; move forward
    # through the machine when possible.
    if current == "DETAILS_GENERATED":
        assignment.registration_status = registration_transition(current, "AWAITING_MANUAL_CONFIGURATION")
        assignment.registration_status = registration_transition(assignment.registration_status, "MANUALLY_CONFIRMED")
    elif current in {"AWAITING_MANUAL_CONFIGURATION", "VERIFICATION_FAILED"}:
        assignment.registration_status = registration_transition(current, "MANUALLY_CONFIRMED")
    else:
        assignment.registration_status = "MANUALLY_CONFIRMED"
    assignment.manual_confirmed = True
    accepted = {key: bool(details.get(key)) for key in ("source_ip_correct", "secret_version_applied", "services_enabled", "primary_configured", "secondary_configured") if details}
    return {"manual_confirmed": True, "confirmation": accepted}


def record_technical_verification(session: Session, nas: Nas, assignment: NasRadiusAssignment, signal: str, detail: dict | None = None) -> dict:
    """Record a functional signal that FreeRADIUS reaches the NAS assignment.

    Supported signals are authentication/accounting requests observed,
    an integration-test result, or a FreeRADIUS callback carrying the NAS
    identity.
    """
    if signal not in VERIFICATION_SIGNALS:
        raise ValueError(f"unsupported verification signal: {signal}")
    current = assignment.registration_status or "PENDING"
    if current not in {"MANUALLY_CONFIRMED", "VERIFICATION_PENDING", "VERIFICATION_FAILED", "VERIFIED"}:
        raise ValueError(f"registration cannot be verified from {current}")
    assignment.registration_status = registration_transition(current, "VERIFICATION_PENDING")
    assignment.registration_status = registration_transition(assignment.registration_status, "VERIFIED")
    assignment.last_verified_at = datetime.now(timezone.utc)
    return {"verified": True, "signal": signal, "detail": detail or {}, "verified_at": assignment.last_verified_at.isoformat()}


def registration_package_text(package: dict) -> str:
    """Render a copyable plain-text registration package for an admin."""
    lines = [
        "# FreeRADIUS NAS registration (manual)",
        f"NAS name: {package.get('nas_name')}",
        f"NAS short name: {package.get('nas_short_name')}",
        f"Source IP/CIDR: {package.get('nas_source_cidr')}",
        f"NAS-Identifier: {package.get('nas_identifier') or ''}",
        f"Vendor: {package.get('vendor')}",
        f"RADIUS server: {package.get('radius_server')}",
        f"Authentication port: {package.get('authentication_port')}",
        f"Accounting port: {package.get('accounting_port')}",
        f"CoA port: {package.get('coa_port')}",
        f"Services: {', '.join(package.get('services') or [])}",
        f"Message-Authenticator: {package.get('message_authenticator_required')}",
        f"Shared secret: {package.get('shared_secret')}",
        f"Secret version: {package.get('secret_version')}",
    ]
    return "\n".join(lines)
