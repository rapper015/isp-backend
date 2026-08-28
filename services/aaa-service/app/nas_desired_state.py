"""Deterministic, secret-free desired-state engine for managed RouterOS AAA.

The engine:

* is pure (no network, no database)
* is idempotent (the same inputs produce the same plan)
* never mutates input state while previewing
* produces stable, ordered change operations
* never emits a shared secret or RouterOS password
"""
from __future__ import annotations

from typing import Any

# Ownership classifications for discovered RouterOS objects.
OWNERSHIP_BACKEND = "BACKEND_MANAGED"
OWNERSHIP_EXTERNAL = "EXTERNALLY_MANAGED"
OWNERSHIP_UNKNOWN = "UNKNOWN"
OWNERSHIP_CONFLICT = "CONFLICTING"

# Risk levels.
RISK_LOW = "low"
RISK_MEDIUM = "medium"
RISK_HIGH = "high"
RISK_CRITICAL = "critical"

# Services the backend may configure on a router.
ALLOWED_SERVICES = {"ppp", "pppoe", "hotspot", "login", "wireless", "dhcp", "ipsec", "dot1x"}

# Operation types.
OP_ADD = "add"
OP_UPDATE = "update"
OP_REMOVE = "remove"
OP_NOOP = "noop"

# Fields of a normalized RADIUS assignment that the backend manages.
_RADIUS_FIELDS = ("address", "services", "auth_port", "accounting_port", "timeout", "src_address")


def build_desired_assignments(assignments: list[Any]) -> list[dict]:
    """Normalize NasRadiusAssignment records into secret-free desired entries.

    ``assignments`` may be ORM objects exposing attributes, or plain dicts.
    Secrets are never included; only ``secret_version`` is carried.
    """
    desired: list[dict] = []
    active = [item for item in assignments if _attr(item, "desired_status") != "disabled"]
    for item in sorted(active, key=lambda entry: _attr(entry, "priority", 100)):
        server = _attr(item, "radius_server")
        host = _attr(server, "host") if server is not None else _attr(item, "address", "")
        if not host and isinstance(item, dict):
            host = str(item.get("radius_server_host") or "")
        desired.append({
            "assignment_id": str(_attr(item, "id")),
            "role": _attr(item, "role", "secondary"),
            "address": host,
            "secret_version": _attr(item, "secret_version", 1),
            "services": list(_attr(item, "services", []) or []),
            "auth_port": _attr(item, "auth_port") or _attr(server, "auth_port") if not isinstance(item, dict) else _attr(item, "auth_port", None),
            "accounting_port": _attr(item, "accounting_port") or (_attr(server, "accounting_port") if server is not None else None),
            "coa_port": _attr(item, "coa_port") or (_attr(server, "coa_port") if server is not None else None),
            "timeout": _attr(item, "timeout_seconds", 3000),
            "src_address": _attr(item, "source_address", None),
        })
    return desired


def _attr(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def classify_radius_entry(entry: dict, desired_addresses: set[str], managed_addresses: set[str]) -> str:
    """Classify a discovered RADIUS entry as backend/external/unknown/conflict.

    A matching desired address is a backend-managed candidate. An address the
    backend previously created (tracked in NasRemoteObject) but no longer
    desires is a backend-managed orphan. Everything else is external.
    """
    address = str(entry.get("address") or "")
    if address in desired_addresses:
        return OWNERSHIP_BACKEND
    if address in managed_addresses:
        return OWNERSHIP_BACKEND
    if not address:
        return OWNERSHIP_UNKNOWN
    return OWNERSHIP_EXTERNAL


def _entry_matches_desired(entry: dict, desired: dict) -> bool:
    """Compare normalized current and desired RADIUS values (secret-free)."""
    return (
        str(entry.get("address") or "") == str(desired.get("address") or "")
        and sorted(entry.get("service") or []) == sorted(desired.get("services") or [])
        and int(entry.get("authentication_port") or 1812) == int(desired.get("auth_port") or 1812)
        and int(entry.get("accounting_port") or 1813) == int(desired.get("accounting_port") or 1813)
        and int(entry.get("timeout") or 3000) == int(desired.get("timeout") or 3000)
        and (entry.get("src_address") or None) == (desired.get("src_address") or None)
    )


def _desired_radius_values(desired: dict) -> dict:
    return {
        "address": desired.get("address", ""),
        "services": desired.get("services", []),
        "auth_port": desired.get("auth_port"),
        "accounting_port": desired.get("accounting_port"),
        "timeout": desired.get("timeout", 3000),
        "src_address": desired.get("src_address"),
    }


def compute_radius_changes(current_entries: list[dict], desired_entries: list[dict],
                           managed_addresses: set[str] | None = None) -> list[dict]:
    """Produce ordered add/update/remove/noop operations for RADIUS entries.

    Additions are emitted before removals so the router never loses its last
    working entry mid-apply.
    """
    managed_addresses = managed_addresses or set()
    desired_addresses = {str(item.get("address") or "") for item in desired_entries}
    changes: list[dict] = []
    matched: set[str] = set()

    for desired in desired_entries:
        address = str(desired.get("address") or "")
        candidate = None
        for entry in current_entries:
            if str(entry.get("address") or "") == address:
                candidate = entry
                break
        if candidate is None:
            changes.append({"operation": OP_ADD, "target": "radius_assignment", "assignment_id": desired.get("assignment_id"), "values": _desired_radius_values(desired)})
        else:
            matched.add(address)
            if _entry_matches_desired(candidate, desired):
                changes.append({"operation": OP_NOOP, "target": "radius_assignment", "assignment_id": desired.get("assignment_id"), "remote_object_id": candidate.get("remote_id"), "values": _desired_radius_values(desired)})
            else:
                changes.append({"operation": OP_UPDATE, "target": "radius_assignment", "assignment_id": desired.get("assignment_id"), "remote_object_id": candidate.get("remote_id"), "values": _desired_radius_values(desired)})

    for entry in current_entries:
        address = str(entry.get("address") or "")
        if address not in desired_addresses and address in managed_addresses and address not in matched:
            changes.append({"operation": OP_REMOVE, "target": "radius_assignment", "remote_object_id": entry.get("remote_id"), "values": {"address": address}})

    return changes


def _ppp_aaa_changes(current: dict, desired: dict) -> list[dict]:
    if not desired.get("ppp_aaa"):
        return []
    wanted = {"use_radius": True, "accounting": desired.get("accounting", True), "interim_update_seconds": desired.get("interim_update_seconds")}
    current_values = {"use_radius": bool(current.get("use_radius")), "accounting": bool(current.get("accounting"))}
    if current_values["use_radius"] and current_values["accounting"] and not wanted["interim_update_seconds"]:
        return [{"operation": OP_NOOP, "target": "ppp_aaa", "values": wanted}]
    if current_values["use_radius"] and current_values["accounting"] and current.get("interim_update_seconds") == wanted["interim_update_seconds"]:
        return [{"operation": OP_NOOP, "target": "ppp_aaa", "values": wanted}]
    return [{"operation": OP_UPDATE, "target": "ppp_aaa", "values": wanted}]


def _user_aaa_changes(current: dict, desired: dict) -> list[dict]:
    if not desired.get("login_radius"):
        return []
    wanted = {
        "use_radius": True,
        "accounting": desired.get("user_aaa_accounting", False),
        "default_group": desired.get("user_aaa_default_group", "full"),
        "excluded_groups": desired.get("user_aaa_excluded_groups", []),
    }
    if bool(current.get("use_radius")) and current.get("default_group") == wanted["default_group"]:
        return [{"operation": OP_NOOP, "target": "user_aaa", "values": wanted}]
    return [{"operation": OP_UPDATE, "target": "user_aaa", "values": wanted}]


def _hotspot_changes(current_profiles: list[dict], desired: dict) -> list[dict]:
    changes: list[dict] = []
    selected = {str(item.get("name")) for item in desired.get("hotspot_profiles", [])}
    desired_by_name = {str(item.get("name")): item for item in desired.get("hotspot_profiles", [])}
    for profile in current_profiles:
        name = str(profile.get("name") or "")
        if name not in selected:
            continue
        wanted = {
            "use_radius": True,
            "radius_accounting": True,
            "radius_interim_update_seconds": desired.get("interim_update_seconds"),
            "location_name": desired_by_name.get(name, {}).get("location_name"),
        }
        matches = bool(profile.get("use_radius")) and bool(profile.get("radius_accounting"))
        changes.append({
            "operation": OP_NOOP if matches else OP_UPDATE,
            "target": "hotspot_profile",
            "profile": name,
            "remote_object_id": profile.get("remote_id"),
            "values": wanted,
        })
    return changes


def _incoming_changes(current: Any, desired: dict) -> list[dict]:
    """Compute incoming CoA changes. ``current`` may be a dict or a list with a
    single incoming-RADIUS row, depending on the adapter normalization."""
    if not desired.get("incoming_coa"):
        return []
    row = current[0] if isinstance(current, list) and current else (current or {})
    wanted = {"accept": True, "port": desired.get("coa_port", 3799), "disabled": False}
    if bool(row.get("accept")):
        return [{"operation": OP_NOOP, "target": "radius_incoming", "values": wanted}]
    return [{"operation": OP_UPDATE, "target": "radius_incoming", "values": wanted}]


def validate_desired(desired: dict, capabilities: dict | None = None, tenant_policy: dict | None = None) -> list[str]:
    """Blocking validation errors. Returns an empty list when valid."""
    errors: list[str] = []
    assignments = desired.get("radius_assignments", [])
    active = [item for item in assignments if item.get("desired_status", "enabled") != "disabled"]
    if not active:
        errors.append("at least one RADIUS assignment is required")
    if desired.get("login_radius"):
        if not desired.get("break_glass_verified"):
            errors.append("login RADIUS requires break-glass administrator verification")
        if not desired.get("acknowledge_login_risk"):
            errors.append("login RADIUS requires explicit risk acknowledgement")
        if not desired.get("user_aaa_default_group"):
            errors.append("login RADIUS requires a default group")
    if desired.get("incoming_coa") and not active:
        errors.append("incoming CoA requires an active RADIUS assignment")
    interval = desired.get("interim_update_seconds")
    if interval is not None:
        policy_max = (tenant_policy or {}).get("interim_update_max_seconds", 86400)
        if not (60 <= int(interval) <= int(policy_max)):
            errors.append("interim update interval is outside tenant policy bounds")
    capabilities = capabilities or {}
    for service in desired.get("services", []):
        if service not in ALLOWED_SERVICES:
            errors.append(f"unsupported service: {service}")
    if capabilities.get("hotspot") is False and desired.get("hotspot_profiles"):
        errors.append("router does not support hotspot RADIUS")
    if capabilities.get("incoming_coa") is False and desired.get("incoming_coa"):
        errors.append("router does not support incoming CoA")
    return errors


def risk_for_desired(desired: dict, changes: list[dict]) -> str:
    """Classify the change plan risk level."""
    if desired.get("login_radius"):
        return RISK_CRITICAL
    removals = [item for item in changes if item.get("operation") == OP_REMOVE]
    updates = [item for item in changes if item.get("operation") == OP_UPDATE]
    for update in updates:
        if update.get("target") == "radius_assignment" and update.get("values", {}).get("address"):
            roles = {item.get("role") for item in desired.get("radius_assignments", [])}
            if "primary" in roles and any(item.get("role") == "primary" and item.get("address") == update["values"]["address"] for item in desired.get("radius_assignments", [])):
                return RISK_HIGH
    for removal in removals:
        if removal.get("target") == "radius_assignment":
            return RISK_HIGH
    if desired.get("interim_update_seconds") is not None:
        return RISK_MEDIUM
    if any(item.get("operation") in {OP_ADD, OP_UPDATE} for item in changes):
        return RISK_LOW
    return RISK_LOW


def compute_plan(current: dict | None, desired: dict, assignments: list[dict],
                 managed_addresses: set[str] | None = None,
                 capabilities: dict | None = None,
                 tenant_policy: dict | None = None) -> dict:
    """Pure planning entry point. Never contacts a router and never mutates input.

    Returns a dict with ``operations``, ``warnings``, ``validation``,
    ``risk`` and ``requires_approval``.
    """
    current = current or {}
    errors = validate_desired(desired, capabilities, tenant_policy)
    warnings: list[str] = []
    changes: list[dict] = []

    current_entries = current.get("radius_entries", [])
    desired_assignments = desired.get("radius_assignments", [])
    changes.extend(compute_radius_changes(current_entries, desired_assignments, managed_addresses or set()))
    changes.extend(_ppp_aaa_changes(current.get("ppp_aaa", {}), desired))
    changes.extend(_user_aaa_changes(current.get("user_aaa", {}), desired))
    changes.extend(_hotspot_changes(current.get("hotspot_profiles", []), desired))
    changes.extend(_incoming_changes(current.get("radius_incoming", {}), desired))

    # Externally managed entries are never modified automatically.
    desired_addresses = {str(item.get("address") or "") for item in desired_assignments}
    for entry in current_entries:
        if classify_radius_entry(entry, desired_addresses, managed_addresses or set()) == OWNERSHIP_EXTERNAL:
            warnings.append(f"RADIUS entry for {entry.get('address')} is externally managed and will not be modified")

    risk = RISK_CRITICAL if errors and desired.get("login_radius") else risk_for_desired(desired, changes)
    requires_approval = risk == RISK_CRITICAL
    validation = {"valid": not errors, "errors": errors, "warnings": warnings, "current_configuration_present": bool(current)}
    return {
        "operations": changes,
        "warnings": warnings,
        "validation": validation,
        "risk": risk,
        "requires_approval": requires_approval,
    }
