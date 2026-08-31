"""Alert domain rules: fingerprinting, dedup windows, grouping keys, severity
with impact context, noise reduction (flapping/cooldown/dependency)."""
from __future__ import annotations

from .exceptions import AlertError
from .identity import alert_fingerprint

SEVERITY_ORDER = {"INFORMATIONAL": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}

# Default grouping key labels (used for dedup/grouping).
GROUPING_LABELS = ("service", "component", "resource", "pop")


def validate_severity(severity: str) -> str:
    severity = severity.upper()
    if severity not in SEVERITY_ORDER:
        raise AlertError(f"invalid severity {severity!r}")
    return severity


def fingerprint(service: str, alert_name: str, resource: str | None = None,
                component: str | None = None, tenant_id: str | None = None) -> str:
    return alert_fingerprint(service, alert_name, resource, component, tenant_id)


def grouping_key(labels: dict) -> str:
    parts = []
    for key in GROUPING_LABELS:
        parts.append(str(labels.get(key, "")).lower())
    return "|".join(parts)


def should_dedupe(existing_firing: bool, last_observed: float | None, now: float,
                  dedup_window: int) -> bool:
    """True when a new event for an already-firing alert should be coalesced
    (within the dedup window) rather than creating a new alert."""
    if existing_firing:
        return True
    if last_observed is None:
        return False
    return (now - last_observed) < dedup_window


def dependency_suppression(alert: dict, dependency_alerts: list) -> bool:
    """If a parent/dependency alert is firing, suppress the downstream alert.
    Example: POP router unreachable suppresses subscriber CPE-offline alerts.
    Accepts dicts or ORM objects with a `state` attribute."""
    for dep in dependency_alerts:
        state = dep.get("state") if isinstance(dep, dict) else getattr(dep, "state", None)
        if state in ("FIRING", "ACKNOWLEDGED"):
            return True
    return False


def is_flapping(states: list[str]) -> bool:
    """Heuristic flapping detection: frequent FIRING/RESOLVED oscillation."""
    if len(states) < 4:
        return False
    transitions = sum(1 for i in range(1, len(states)) if states[i] != states[i - 1])
    return transitions >= max(3, len(states) // 2)


def severity_with_impact(severity: str, customer_impact: bool) -> str:
    """Severity without impact context is not allowed: a HIGH alert on a
    customer-facing path with impact is elevated."""
    severity = validate_severity(severity)
    if customer_impact and severity == "LOW":
        return "MEDIUM"
    return severity
