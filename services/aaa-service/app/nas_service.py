"""NAS/MikroTik orchestration: connection test, discovery, apply, verify,
rollback and health checks.

All router interaction happens through a ``RouterOSAdapter``. Secrets are
decrypted only inside this module for the duration of an operation and are
never persisted, published or returned.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from os import getenv
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .circuit_breaker import record_failure, record_success
from .models import Nas, NasCapability, NasCredential, NasDesiredConfiguration, NasHealthCheck, NasRadiusAssignment, NasRemoteObject, NasSnapshot
from .nas_desired_state import OWNERSHIP_BACKEND, OWNERSHIP_EXTERNAL, build_desired_assignments, classify_radius_entry, compute_plan
from .nas_drift import detect_drift
from .nas_planning import configuration_hash, sanitize_configuration
from .routeros import (FakeRouterOSAdapter, RouterOSAdapter, RouterOSError, RouterOSUnsupportedVersion, adapter_for_version, parse_routeros_version, redact, validate_management_host, validate_port, validate_radius_source_ip)
from .security import decrypt_secret

SAFE_ERROR_MESSAGES = {
    "DNS_FAILURE": "management hostname could not be resolved",
    "NETWORK_UNREACHABLE": "management network is unreachable",
    "CONNECTION_REFUSED": "management connection was refused",
    "CONNECTION_TIMEOUT": "management connection timed out",
    "TLS_FAILURE": "TLS negotiation with the router failed",
    "AUTHENTICATION_FAILED": "RouterOS authentication failed",
    "INSUFFICIENT_PERMISSION": "the RouterOS user lacks required permissions",
    "UNSUPPORTED_ROUTEROS_VERSION": "the RouterOS version is not supported",
    "UNSUPPORTED_DEVICE": "the device is not a supported MikroTik router",
    "INVALID_RESPONSE": "the router returned an unexpected response",
    "COMMAND_FAILED": "the router rejected a configuration command",
    "CONNECTION_FAILED": "management connection failed",
}


def build_adapter(nas: Nas, credential: NasCredential | None) -> RouterOSAdapter:
    """Build the appropriate adapter for a NAS. The fake backend is used only
    when explicitly enabled for tests/simulations."""
    username = decrypt_secret(credential.username_ciphertext) if credential else "admin"
    password = decrypt_secret(credential.secret_ciphertext) if credential else ""
    host = nas.management_host or nas.source_ip
    port = credential.api_port if credential and credential.api_port else nas.management_port
    tls = dict(credential.tls_settings or {}) if credential else {}
    if getenv("AAA_ROUTEROS_ADAPTER", "").lower() == "fake":
        fake = FakeRouterOSAdapter()
        if credential is None or username == "invalid" or password == "invalid":
            fake.fail_auth = True
        return fake
    version = nas.routeros_version
    try:
        parsed = parse_routeros_version(version or "")
        if parsed and parsed[0] in (6, 7):
            return adapter_for_version(version, host=host, username=username, password=password, port=port, use_ssl=nas.management_protocol == "api_ssl", tls_verify=bool(tls.get("verify", nas.tls_verify)), verify_hostname=bool(tls.get("verify_hostname", True)), connection_timeout=float(getenv("AAA_ROUTEROS_CONNECT_TIMEOUT", "5")), command_timeout=float(getenv("AAA_ROUTEROS_COMMAND_TIMEOUT", "10")))
        return adapter_for_version(version, host=host, username=username, password=password, port=port, use_ssl=nas.management_protocol == "api_ssl", tls_verify=bool(tls.get("verify", nas.tls_verify)), verify_hostname=bool(tls.get("verify_hostname", True)))
    except RouterOSUnsupportedVersion:
        # A version may not be known yet; attempt a generic adapter.
        from .routeros import RouterOSApiAdapter
        return RouterOSApiAdapter(host=host, username=username, password=password, port=port, use_ssl=nas.management_protocol == "api_ssl", tls_verify=bool(tls.get("verify", nas.tls_verify)), verify_hostname=bool(tls.get("verify_hostname", True)))


def safe_routeros_error(error: RouterOSError) -> str:
    """Return a stable safe message for a structured RouterOS error."""
    return SAFE_ERROR_MESSAGES.get(error.code, SAFE_ERROR_MESSAGES["CONNECTION_FAILED"])


def record_health(session: Session, nas_id, check_type: str, status: str, diagnostic: dict | None = None,
                  latency_ms: float | None = None, failure_reason: str | None = None) -> None:
    session.add(NasHealthCheck(nas_id=nas_id, check_type=check_type, status=status, started_at=datetime.now(timezone.utc), completed_at=datetime.now(timezone.utc), latency_ms=latency_ms, diagnostic=diagnostic or {}, failure_reason=failure_reason))


# ---------------------------------------------------------------------------
# Connection testing
# ---------------------------------------------------------------------------

def test_nas_connection(session: Session, nas: Nas, adapter: RouterOSAdapter) -> dict:
    """Run a live connection test. Never mutates router configuration."""
    try:
        result = adapter.test_connection()
    except RouterOSError as error:
        code = error.code
        nas.connection_status = "FAILED"
        nas.health = "unhealthy"
        nas.failure_reason = code
        record_health(session, nas.id, "api_connectivity", "FAIL", failure_reason=code)
        record_failure(nas.id)
        session.commit()
        return {"ok": False, "error": code, "message": safe_routeros_error(error)}
    version = str(result.get("version") or "")
    parsed = parse_routeros_version(version)
    if parsed is None or parsed[0] not in (6, 7):
        nas.connection_status = "FAILED"
        nas.health = "unhealthy"
        nas.failure_reason = "UNSUPPORTED_ROUTEROS_VERSION"
        record_health(session, nas.id, "api_connectivity", "FAIL", failure_reason="UNSUPPORTED_ROUTEROS_VERSION")
        record_failure(nas.id)
        session.commit()
        return {"ok": False, "error": "UNSUPPORTED_ROUTEROS_VERSION", "message": SAFE_ERROR_MESSAGES["UNSUPPORTED_ROUTEROS_VERSION"]}
    now = datetime.now(timezone.utc)
    nas.connection_status = "CONNECTED"
    nas.health = "healthy"
    nas.last_connected_at = now
    nas.routeros_version = version
    nas.identity = str(result.get("identity") or nas.identity or "")
    nas.failure_reason = None
    record_health(session, nas.id, "api_connectivity", "PASS", {"identity": nas.identity, "version": version}, latency_ms=result.get("latency_ms"))
    record_success(nas.id)
    session.commit()
    return {"ok": True, "identity": nas.identity, "version": version, "latency_ms": result.get("latency_ms")}


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def _normalize_identity_fields(nas: Nas, resource: dict) -> None:
    nas.identity = str(resource.get("identity") or nas.identity or "")
    nas.routeros_version = str(resource.get("version") or nas.routeros_version or "")
    board = (resource.get("system_resource") or {}).get("board_name")
    architecture = (resource.get("system_resource") or {}).get("architecture_name")
    if board:
        nas.board_name = str(board)
    if architecture:
        nas.architecture = str(architecture)
    nas.time_zone = str((resource.get("system_resource") or {}).get("time_zone_name") or nas.time_zone or "") or None


def discover_nas(session: Session, nas: Nas, adapter: RouterOSAdapter) -> dict:
    """Read current RouterOS state, detect capabilities, store a redacted
    snapshot and remote-object ownership records."""
    resource = adapter.get_relevant_service_state()
    capabilities = adapter.detect_capabilities()
    _normalize_identity_fields(nas, resource)
    nas.capabilities = capabilities
    nas.last_discovery_at = datetime.now(timezone.utc)
    nas.configuration_status = "PENDING" if nas.configuration_status == "NONE" else nas.configuration_status

    # Capability record (versioned).
    version = str(resource.get("version") or "0")
    session.add(NasCapability(nas_id=nas.id, version=version, flags=capabilities, raw=redact(resource)))

    # Redacted snapshot.
    sanitized = sanitize_configuration(resource)
    checksum = configuration_hash(sanitized)
    highest = session.scalar(select(NasSnapshot.version).where(NasSnapshot.nas_id == nas.id).order_by(NasSnapshot.version.desc()).limit(1)) or 0
    session.add(NasSnapshot(nas_id=nas.id, version=highest + 1, scope="radius_aaa", source="discovery", sanitized_configuration=sanitized, configuration_hash=checksum))

    # Remote object ownership classification.
    desired_addresses: set[str] = set()
    for assignment in session.scalars(select(NasRadiusAssignment).where(NasRadiusAssignment.nas_id == nas.id)):
        server = assignment.radius_server
        if server is not None:
            desired_addresses.add(str(server.host))
    managed_addresses = {str(item.remote_object_id) for item in session.scalars(select(NasRemoteObject).where(NasRemoteObject.nas_id == nas.id, NasRemoteObject.object_type == "radius_entry"))}
    for entry in resource.get("radius_entries", []):
        ownership = classify_radius_entry(entry, desired_addresses, managed_addresses)
        object_type, remote_id = "radius_entry", str(entry.get("remote_id") or "")
        existing = session.scalar(select(NasRemoteObject).where(NasRemoteObject.nas_id == nas.id, NasRemoteObject.object_type == object_type, NasRemoteObject.remote_object_id == remote_id))
        if existing:
            existing.last_observed_attributes = {key: value for key, value in entry.items() if key != "remote_id"}
            existing.last_observed_at = datetime.now(timezone.utc)
            existing.ownership = ownership
        else:
            session.add(NasRemoteObject(nas_id=nas.id, object_type=object_type, remote_object_id=remote_id, last_observed_attributes={key: value for key, value in entry.items() if key != "remote_id"}, last_observed_at=datetime.now(timezone.utc), ownership=ownership))

    session.commit()
    return {"ok": True, "identity": nas.identity, "version": nas.routeros_version, "capabilities": capabilities, "snapshot_checksum": checksum}


# ---------------------------------------------------------------------------
# Desired state loading
# ---------------------------------------------------------------------------

def load_desired_state(session: Session, nas: Nas) -> tuple[dict | None, list[NasRadiusAssignment]]:
    desired = session.scalar(select(NasDesiredConfiguration).where(NasDesiredConfiguration.nas_id == nas.id, NasDesiredConfiguration.status == "active").order_by(NasDesiredConfiguration.version.desc()))
    assignments = list(session.scalars(select(NasRadiusAssignment).where(NasRadiusAssignment.nas_id == nas.id).order_by(NasRadiusAssignment.priority)))
    return (desired.configuration if desired else None), assignments


def decrypt_assignment_secrets(assignments: list[NasRadiusAssignment]) -> dict[str, str]:
    return {str(item.id): decrypt_secret(item.secret_ciphertext) for item in assignments}


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------

def apply_operations(session: Session, nas: Nas, adapter: RouterOSAdapter, operations: list[dict], secrets: dict[str, str]) -> dict:
    """Apply planned operations. Additions precede removals; externally managed
    objects are never touched. Returns a summary of applied operations."""
    summary: list[dict] = []
    ordered = sorted(operations, key=lambda op: 0 if op["operation"] == "add" else 1 if op["operation"] == "update" else 2)
    for op in ordered:
        operation, target = op["operation"], op["target"]
        values = op.get("values", {})
        if operation == "noop":
            summary.append({"operation": "noop", "target": target})
            continue
        if operation == "add" and target == "radius_assignment":
            assignment_id = op.get("assignment_id")
            secret = secrets.get(str(assignment_id), "")
            remote_id = adapter.create_radius_entry({**values, "secret": secret})
            _upsert_remote_object(session, nas.id, "radius_entry", remote_id, assignment_id, values)
            summary.append({"operation": "add", "target": target, "assignment_id": assignment_id, "remote_object_id": remote_id})
        elif operation == "update" and target == "radius_assignment":
            assignment_id = op.get("assignment_id")
            remote_id = op.get("remote_object_id")
            secret = secrets.get(str(assignment_id), "")
            adapter.update_radius_entry(remote_id, {**values, "secret": secret})
            _upsert_remote_object(session, nas.id, "radius_entry", remote_id, assignment_id, values)
            summary.append({"operation": "update", "target": target, "assignment_id": assignment_id, "remote_object_id": remote_id})
        elif operation == "remove" and target == "radius_assignment":
            remote_id = op.get("remote_object_id")
            adapter.remove_radius_entry(remote_id)
            _drop_remote_object(session, nas.id, "radius_entry", remote_id)
            summary.append({"operation": "remove", "target": target, "remote_object_id": remote_id})
        elif operation == "update" and target == "ppp_aaa":
            adapter.configure_ppp_aaa(values)
            summary.append({"operation": "update", "target": "ppp_aaa"})
        elif operation == "update" and target == "user_aaa":
            adapter.configure_user_aaa(values)
            summary.append({"operation": "update", "target": "user_aaa"})
        elif operation == "update" and target == "hotspot_profile":
            adapter.configure_hotspot_radius(op.get("profile", ""), values)
            summary.append({"operation": "update", "target": "hotspot_profile", "profile": op.get("profile")})
        elif operation == "update" and target == "radius_incoming":
            adapter.configure_radius_incoming(values)
            summary.append({"operation": "update", "target": "radius_incoming"})
    return {"applied": summary}


def _upsert_remote_object(session: Session, nas_id, object_type: str, remote_id: str, assignment_id, values: dict) -> None:
    from uuid import UUID as _UUID
    try:
        backend_id = _UUID(str(assignment_id)) if assignment_id else None
    except (TypeError, ValueError):
        backend_id = None
    existing = session.scalar(select(NasRemoteObject).where(NasRemoteObject.nas_id == nas_id, NasRemoteObject.object_type == object_type, NasRemoteObject.remote_object_id == remote_id))
    if existing:
        existing.backend_assignment_id = backend_id
        existing.last_observed_attributes = {key: value for key, value in values.items() if value is not None}
        existing.last_observed_at = datetime.now(timezone.utc)
        existing.ownership = OWNERSHIP_BACKEND
    else:
        session.add(NasRemoteObject(nas_id=nas_id, object_type=object_type, remote_object_id=remote_id, backend_assignment_id=backend_id, last_observed_attributes={key: value for key, value in values.items() if value is not None}, last_observed_at=datetime.now(timezone.utc), ownership=OWNERSHIP_BACKEND))


def _drop_remote_object(session: Session, nas_id, object_type: str, remote_id: str) -> None:
    item = session.scalar(select(NasRemoteObject).where(NasRemoteObject.nas_id == nas_id, NasRemoteObject.object_type == object_type, NasRemoteObject.remote_object_id == remote_id))
    if item is not None:
        session.delete(item)


def _desired_with_assignments(desired_configuration: dict, assignments: list[NasRadiusAssignment]) -> dict:
    """Merge the normalized RADIUS assignments into the desired configuration."""
    desired = dict(desired_configuration)
    desired["radius_assignments"] = build_desired_assignments(assignments)
    return desired


def apply_nas_configuration(session: Session, nas: Nas, adapter: RouterOSAdapter, desired_configuration: dict, assignments: list[NasRadiusAssignment], tenant_policy: dict | None = None) -> dict:
    """Re-read current state, plan, apply and re-verify in one bounded flow."""
    current = adapter.get_relevant_service_state()
    secrets = decrypt_assignment_secrets(assignments)
    desired = _desired_with_assignments(desired_configuration, assignments)
    managed_addresses = {str(item.remote_object_id) for item in session.scalars(select(NasRemoteObject).where(NasRemoteObject.nas_id == nas.id, NasRemoteObject.object_type == "radius_entry"))}
    plan = compute_plan(current, desired, assignments, managed_addresses, nas.capabilities, tenant_policy)
    if not plan["validation"]["valid"]:
        return {"ok": False, "valid": False, "errors": plan["validation"]["errors"], "applied": []}
    result = apply_operations(session, nas, adapter, plan["operations"], secrets)
    verified = verify_nas_configuration(session, nas, adapter, desired_configuration, assignments, tenant_policy)
    return {"ok": True, "valid": True, "applied": result["applied"], "verified": verified.get("matched", False), "differences": verified.get("differences", [])}


def verify_nas_configuration(session: Session, nas: Nas, adapter: RouterOSAdapter, desired_configuration: dict, assignments: list[NasRadiusAssignment], tenant_policy: dict | None = None) -> dict:
    """Read the configuration again and compare it with the desired state."""
    current = adapter.get_relevant_service_state()
    desired = _desired_with_assignments(desired_configuration, assignments)
    managed_addresses = {str(item.remote_object_id) for item in session.scalars(select(NasRemoteObject).where(NasRemoteObject.nas_id == nas.id, NasRemoteObject.object_type == "radius_entry"))}
    plan = compute_plan(current, desired, assignments, managed_addresses, nas.capabilities, tenant_policy)
    differences = [op for op in plan["operations"] if op["operation"] != "noop"]
    matched = not differences
    sanitized = sanitize_configuration(current)
    checksum = configuration_hash(sanitized)
    highest = session.scalar(select(NasSnapshot.version).where(NasSnapshot.nas_id == nas.id).order_by(NasSnapshot.version.desc()).limit(1)) or 0
    session.add(NasSnapshot(nas_id=nas.id, version=highest + 1, scope="radius_aaa", source="verification", sanitized_configuration=sanitized, configuration_hash=checksum))
    if matched:
        nas.last_verified_at = datetime.now(timezone.utc)
        nas.configuration_status = "VERIFIED"
    return {"matched": matched, "differences": differences, "snapshot_checksum": checksum, "desired_missing": [op for op in differences if op.get("operation") == "add"]}


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------

def rollback_nas_configuration(session: Session, nas: Nas, adapter: RouterOSAdapter, tenant_policy: dict | None = None) -> dict:
    """Restore the previous backend-managed snapshot. Only the managed scope is
    touched; the router is never re-imaged."""
    previous = session.scalar(select(NasSnapshot).where(NasSnapshot.nas_id == nas.id, NasSnapshot.source == "verification").order_by(NasSnapshot.version.desc()).offset(1).limit(1)) or session.scalar(select(NasSnapshot).where(NasSnapshot.nas_id == nas.id, NasSnapshot.source == "discovery").order_by(NasSnapshot.version.desc()).limit(1))
    if previous is None:
        return {"ok": False, "error": "no rollback snapshot available"}
    snapshot = previous.sanitized_configuration or {}
    # Rebuild a desired state that matches the snapshot: keep assignments but
    # request the snapshot's radius entries and AAA settings.
    assignments = list(session.scalars(select(NasRadiusAssignment).where(NasRadiusAssignment.nas_id == nas.id)))
    desired = {
        "radius_assignments": _desired_from_snapshot(snapshot),
        "ppp_aaa": bool((snapshot.get("ppp_aaa") or {}).get("use_radius")),
        "incoming_coa": bool((snapshot.get("radius_incoming") or {}).get("accept")),
        "services": [service for entry in snapshot.get("radius_entries", []) for service in (entry.get("service") or [])],
    }
    secrets = decrypt_assignment_secrets(assignments)
    current = adapter.get_relevant_service_state()
    plan = compute_plan(current, desired, [], set(), nas.capabilities, tenant_policy)
    # Rollback only touches objects the backend created previously.
    managed_ids = {str(item.remote_object_id) for item in session.scalars(select(NasRemoteObject).where(NasRemoteObject.nas_id == nas.id, NasRemoteObject.object_type == "radius_entry"))}
    ops = [op for op in plan["operations"] if op.get("target") != "radius_assignment" or op.get("remote_object_id") in managed_ids]
    result = apply_operations(session, nas, adapter, ops, secrets)
    return {"ok": True, "applied": result["applied"]}


def _desired_from_snapshot(snapshot: dict) -> list[dict]:
    desired: list[dict] = []
    for index, entry in enumerate(snapshot.get("radius_entries", [])):
        desired.append({
            "assignment_id": f"rollback-{index}",
            "role": "primary" if index == 0 else "secondary",
            "address": str(entry.get("address") or ""),
            "secret_version": 1,
            "services": entry.get("service") or [],
            "auth_port": entry.get("authentication_port"),
            "accounting_port": entry.get("accounting_port"),
            "coa_port": None,
            "timeout": entry.get("timeout"),
            "src_address": entry.get("src_address"),
        })
    return desired


# ---------------------------------------------------------------------------
# Health checks
# ---------------------------------------------------------------------------

def run_nas_health_check(session: Session, nas: Nas, adapter: RouterOSAdapter, desired_configuration: dict | None, assignments: list[NasRadiusAssignment], tenant_policy: dict | None = None) -> dict:
    """Scheduled health checks. Intervals are controlled by the scheduler and
    never overload the router with polling."""
    checks: list[dict] = []
    try:
        result = adapter.test_connection()
        checks.append({"check_type": "api_connectivity", "status": "PASS", "latency_ms": result.get("latency_ms")})
        record_health(session, nas.id, "api_connectivity", "PASS", {"version": result.get("version")}, latency_ms=result.get("latency_ms"))
    except RouterOSError as error:
        record_health(session, nas.id, "api_connectivity", "FAIL", failure_reason=error.code)
        checks.append({"check_type": "api_connectivity", "status": "FAIL", "reason": error.code})
        nas.connection_status = "FAILED"
        nas.health = "unhealthy"
        session.commit()
        return {"ok": False, "checks": checks}
    identity = adapter.get_identity()
    if identity != nas.identity:
        record_health(session, nas.id, "identity", "WARN", {"expected": nas.identity, "observed": identity})
        checks.append({"check_type": "identity", "status": "WARN", "detail": "router identity changed"})
    else:
        record_health(session, nas.id, "identity", "PASS", {"identity": identity})
        checks.append({"check_type": "identity", "status": "PASS"})
    version = adapter.get_version()
    if parse_routeros_version(version) is None or parse_routeros_version(version)[0] not in (6, 7):
        record_health(session, nas.id, "version", "FAIL", {"version": version})
        checks.append({"check_type": "version", "status": "FAIL", "detail": version})
    else:
        record_health(session, nas.id, "version", "PASS", {"version": version})
        checks.append({"check_type": "version", "status": "PASS"})
    if desired_configuration:
        current = adapter.get_relevant_service_state()
        drift = detect_drift(current, _desired_with_assignments(desired_configuration, assignments), set())
        classification = drift["classification"]
        record_health(session, nas.id, "drift", "PASS" if classification == "NONE" else "FAIL", {"classification": classification, "items": drift["items"]})
        checks.append({"check_type": "drift", "status": "PASS" if classification == "NONE" else "FAIL", "classification": classification})
        desired_addresses = {str(item.get("address") or "") for item in desired_configuration.get("radius_assignments", [])}
        for assignment in desired_addresses:
            if not any(str(entry.get("address") or "") == assignment for entry in current.get("radius_entries", [])):
                record_health(session, nas.id, "radius_assignment_presence", "FAIL", {"address": assignment})
                checks.append({"check_type": "radius_assignment_presence", "status": "FAIL", "detail": assignment})
    healthy = all(check.get("status") == "PASS" for check in checks if check.get("check_type") in {"api_connectivity", "identity", "version", "drift"})
    nas.health = "healthy" if healthy else "degraded"
    nas.connection_status = "CONNECTED"
    nas.last_connected_at = datetime.now(timezone.utc)
    session.commit()
    return {"ok": healthy, "checks": checks}


# ---------------------------------------------------------------------------
# Shared validation helpers
# ---------------------------------------------------------------------------

def validate_nas_management_inputs(session: Session, tenant_id, payload: dict) -> tuple[dict | None, str | None]:
    """Validate management address, ports, tenant ownership and duplicates."""
    try:
        management_host = validate_management_host(str(payload.get("management_host") or ""))
    except ValueError as error:
        return None, str(error)
    try:
        management_port = validate_port(payload.get("management_port", 8729), 8729)
    except ValueError as error:
        return None, str(error)
    try:
        radius_source_ip = validate_radius_source_ip(str(payload.get("radius_source_ip") or ""))
    except ValueError as error:
        return None, str(error)
    duplicate_host = session.scalar(select(Nas.id).where(Nas.management_host == management_host, Nas.tenant_id == tenant_id, Nas.id != payload.get("id")))
    if duplicate_host:
        return None, "duplicate management address for tenant"
    duplicate_source = session.scalar(select(Nas.id).where(Nas.source_ip == radius_source_ip, Nas.tenant_id == tenant_id, Nas.id != payload.get("id")))
    if duplicate_source:
        return None, "duplicate RADIUS source IP for tenant"
    return {"management_host": management_host, "management_port": management_port, "radius_source_ip": radius_source_ip}, None
