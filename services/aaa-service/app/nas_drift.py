"""Drift detection between desired state and the live RouterOS configuration.

Classification is deliberately conservative: externally managed changes are
reported but never automatically overwritten unless reconciliation is
explicitly enabled by tenant policy.
"""
from __future__ import annotations

from datetime import datetime, timezone

DRIFT_NONE = "NONE"
DRIFT_SAFE = "SAFE"
DRIFT_WARNING = "WARNING"
DRIFT_CRITICAL = "CRITICAL"
DRIFT_UNKNOWN = "UNKNOWN"


def _radius_fields_match(entry: dict, desired: dict) -> list[str]:
    differences: list[str] = []
    if str(entry.get("address") or "") != str(desired.get("address") or ""):
        differences.append("address")
    if sorted(entry.get("service") or []) != sorted(desired.get("services") or []):
        differences.append("service_list")
    if int(entry.get("authentication_port") or 1812) != int(desired.get("auth_port") or 1812):
        differences.append("authentication_port")
    if int(entry.get("accounting_port") or 1813) != int(desired.get("accounting_port") or 1813):
        differences.append("accounting_port")
    if int(entry.get("timeout") or 3000) != int(desired.get("timeout") or 3000):
        differences.append("timeout")
    if (entry.get("src_address") or None) != (desired.get("src_address") or None):
        differences.append("src_address")
    return differences


def detect_drift(current: dict | None, desired: dict, managed_addresses: set[str] | None = None) -> dict:
    """Compare desired state against a fresh current snapshot.

    ``current`` is a normalized router state read at check time. The result is
    deterministic and secret-free.
    """
    items: list[dict] = []
    if not current:
        return {"classification": DRIFT_UNKNOWN, "items": [{"kind": "no_current_state", "severity": DRIFT_UNKNOWN, "detail": "router state could not be read"}], "detected_at": datetime.now(timezone.utc).isoformat()}

    managed_addresses = managed_addresses or set()
    current_entries = {str(entry.get("address") or ""): entry for entry in current.get("radius_entries", [])}

    for assignment in desired.get("radius_assignments", []):
        address = str(assignment.get("address") or "")
        entry = current_entries.get(address)
        if entry is None:
            items.append({"kind": "missing_radius_entry", "severity": DRIFT_CRITICAL, "detail": f"RADIUS entry {address} is missing", "assignment_id": assignment.get("assignment_id")})
            continue
        differences = _radius_fields_match(entry, assignment)
        for difference in differences:
            items.append({"kind": f"radius_entry_{difference}", "severity": DRIFT_WARNING, "detail": f"RADIUS entry {address} {difference.replace('_', ' ')} changed", "assignment_id": assignment.get("assignment_id")})

    # A previously backend-managed remote object that vanished is notable even
    # when a matching desired entry exists; ownership tracking may be stale.
    for address in managed_addresses:
        if address not in current_entries and not any(str(item.get("address") or "") == address for item in desired.get("radius_assignments", [])):
            items.append({"kind": "remote_object_removed", "severity": DRIFT_WARNING, "detail": f"managed RADIUS object {address} was removed"})

    # Unknown external entries are informational unless they conflict.
    desired_addresses = {str(item.get("address") or "") for item in desired.get("radius_assignments", [])}
    for address, entry in current_entries.items():
        if address not in desired_addresses and address not in managed_addresses:
            items.append({"kind": "unknown_external_entry", "severity": DRIFT_SAFE, "detail": f"externally managed RADIUS entry {address} present"})

    ppp_current = current.get("ppp_aaa", {})
    if desired.get("ppp_aaa") and not (bool(ppp_current.get("use_radius")) and bool(ppp_current.get("accounting"))):
        items.append({"kind": "ppp_aaa_changed", "severity": DRIFT_WARNING, "detail": "PPP AAA RADIUS settings changed"})

    user_current = current.get("user_aaa", {})
    if desired.get("login_radius") and not bool(user_current.get("use_radius")):
        items.append({"kind": "user_aaa_changed", "severity": DRIFT_CRITICAL, "detail": "router administrative login RADIUS changed"})

    for profile in desired.get("hotspot_profiles", []):
        name = str(profile.get("name") or "")
        matched = next((item for item in current.get("hotspot_profiles", []) if str(item.get("name") or "") == name), None)
        if matched is None or not bool(matched.get("use_radius")):
            items.append({"kind": "hotspot_radius_changed", "severity": DRIFT_WARNING, "detail": f"hotspot profile {name} RADIUS changed"})

    incoming = current.get("radius_incoming", {})
    incoming_row = incoming[0] if isinstance(incoming, list) and incoming else (incoming or {})
    if desired.get("incoming_coa") and not bool(incoming_row.get("accept")):
        items.append({"kind": "incoming_coa_disabled", "severity": DRIFT_WARNING, "detail": "incoming CoA is disabled"})

    severities = {item["severity"] for item in items}
    if DRIFT_CRITICAL in severities:
        classification = DRIFT_CRITICAL
    elif DRIFT_WARNING in severities:
        classification = DRIFT_WARNING
    elif DRIFT_SAFE in severities:
        classification = DRIFT_SAFE
    elif not items:
        classification = DRIFT_NONE
    else:
        classification = DRIFT_UNKNOWN

    return {"classification": classification, "items": items, "detected_at": datetime.now(timezone.utc).isoformat()}
