"""Safe-label and cardinality policy for telemetry (Milestone 9 §11).

Metric labels are restricted to controlled dimensions. High-cardinality or
sensitive identifiers (customer IDs, usernames, order/trace/session ids, IPs,
MACs, secrets) are rejected as labels — they belong in logs/traces for
individual investigation."""
from __future__ import annotations

import re

from .exceptions import CardinalityError

# Safe label keys (metric dimensions).
SAFE_LABELS = (
    "service", "environment", "region", "tenant_tier", "operation", "result",
    "error_class", "device_model", "access_technology", "severity", "component",
    "resource", "pop", "provider",
)

_HIGH_CARDINALITY_PATTERNS = (
    re.compile(r"(?i)^(customer|subscriber|username|user|ticket|order|trace|session|invoice|serial)"),
    re.compile(r"(?i)(_id|uuid|guid)$"),
    re.compile(r"(?i)(mac|ip_address|ip)$"),
)

_SENSITIVE = re.compile(r"(?i)(password|passwd|secret|token|api[_-]?key|authorization|"
                        r"radius[_-]?secret|pan|aadhaar|bank_ref|account_number|otp)")


def validate_label(name: str, value) -> None:
    """Reject high-cardinality/sensitive metric label values."""
    if value is None:
        return
    if name not in SAFE_LABELS:
        raise CardinalityError(f"label {name!r} is not in the approved label set")
    text = str(value).strip().lower()
    if not text:
        return
    if _SENSITIVE.search(text):
        raise CardinalityError(f"label {name!r} contains a sensitive value")
    if any(p.search(text) for p in _HIGH_CARDINALITY_PATTERNS):
        raise CardinalityError(f"label {name!r} has a high-cardinality value")


def assert_safe_labels(labels: dict) -> None:
    for name, value in (labels or {}).items():
        validate_label(name, value)


def normalize_alert_name(name: str) -> str:
    """Stable alert names: lowercase, alphanumeric + underscores."""
    return re.sub(r"[^a-z0-9_]+", "_", (name or "").strip().lower()).strip("_")


def alert_fingerprint(service: str, alert_name: str, resource: str | None,
                      component: str | None, tenant_id: str | None) -> str:
    """Stable fingerprint from normalized labels only (no timestamps/random)."""
    parts = [normalize_alert_name(service), normalize_alert_name(alert_name),
             normalize_alert_name(resource or ""), normalize_alert_name(component or "")]
    parts.append(str(tenant_id) if tenant_id else "platform")
    return "|".join(parts)
