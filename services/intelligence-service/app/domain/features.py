"""Feature transformation registry.

Defines deterministic transformations from analytical records to feature
values. Transformations are versioned with the feature definition; the same
code path is used for training (offline) and serving (online) to prevent
training-serving skew.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .exceptions import FeatureError

# Registry of (feature_name, version) -> callable(record) -> value|None
_TRANSFORMS: dict[tuple[str, str], callable] = {}


def _tz(dt) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def register(feature_name: str, version: str = "v1"):
    def deco(fn):
        _TRANSFORMS[(feature_name, version)] = fn
        return fn
    return deco


def apply_transform(feature_name: str, version: str, record: dict) -> float | str | None:
    key = (feature_name, version)
    if key not in _TRANSFORMS:
        raise FeatureError(f"no transform registered for {feature_name}@{version}")
    try:
        return _TRANSFORMS[key](record)
    except Exception as error:  # noqa: BLE001
        raise FeatureError(f"transform failed for {feature_name}@{version}: {error}") from error


def has_transform(feature_name: str, version: str) -> bool:
    return (feature_name, version) in _TRANSFORMS


# ---- Example transforms (extendable) ----

@register("payment_failure_rate", "v1")
def _payment_failure_rate(records: list[dict]) -> float:
    """Fraction of captured-vs-failed payment events for an entity window."""
    if not records:
        return 0.0
    failed = sum(1 for r in records if r.get("contract") == "billing.payment.failed.v1")
    captured = sum(1 for r in records if r.get("contract") == "billing.payment.captured.v1")
    total = failed + captured
    return round(failed / total, 4) if total else 0.0


@register("recent_payment_failures", "v1")
def _recent_payment_failures(records: list[dict]) -> int:
    return sum(1 for r in records if r.get("contract") == "billing.payment.failed.v1")


@register("auth_failure_rate", "v1")
def _auth_failure_rate(records: list[dict]) -> float:
    if not records:
        return 0.0
    bad = sum(1 for r in records if r.get("outcome") == "FAIL" or r.get("status") == "FAILED")
    return round(bad / len(records), 4)


@register("session_reset_rate", "v1")
def _session_reset_rate(records: list[dict]) -> float:
    if not records:
        return 0.0
    resets = sum(1 for r in records if r.get("reset", False) or r.get("reason") == "RESET")
    return round(resets / len(records), 4)


@register("concurrent_session_count", "v1")
def _concurrent_session_count(records: list[dict]) -> float:
    peak = 0
    for r in records:
        peak = max(peak, int(r.get("concurrent", 0) or 0))
    return float(peak)


@register("mac_churn_count", "v1")
def _mac_churn_count(records: list[dict]) -> int:
    return sum(1 for r in records if r.get("mac_changed", False))


@register("usage_gb", "v1")
def _usage_gb(records: list[dict]) -> float:
    return float(sum(r.get("usage_gb", 0.0) or 0.0 for r in records))


@register("usage_vs_plan_ratio", "v1")
def _usage_vs_plan_ratio(records: list[dict]) -> float:
    usage = sum(r.get("usage_gb", 0.0) or 0.0 for r in records)
    plan = max(float(records[-1].get("plan_gb", 0.0) or 0.0) if records else 0.0, 1.0)
    return round(usage / plan, 4)


@register("support_ticket_count", "v1")
def _support_ticket_count(records: list[dict]) -> int:
    return sum(1 for r in records if r.get("contract") in ("ticket.created.v1",))


@register("sla_breach_count", "v1")
def _sla_breach_count(records: list[dict]) -> int:
    return sum(1 for r in records if r.get("sla_breach", False))


@register("outage_exposure_count", "v1")
def _outage_exposure_count(records: list[dict]) -> int:
    return sum(1 for r in records if r.get("outage", False) or r.get("impact_kind") == "INTERNET")


@register("device_offline_ratio", "v1")
def _device_offline_ratio(records: list[dict]) -> float:
    if not records:
        return 0.0
    off = sum(1 for r in records if r.get("contract") == "device.cpe.offline.v1")
    on = sum(1 for r in records if r.get("contract") == "device.cpe.online.v1")
    return round(off / max(off + on, 1), 4)


@register("latency_avg_ms", "v1")
def _latency_avg(records: list[dict]) -> float:
    values = [float(r.get("latency_ms", 0.0) or 0.0) for r in records]
    return round(sum(values) / len(values), 2) if values else 0.0


@register("error_rate", "v1")
def _error_rate(records: list[dict]) -> float:
    if not records:
        return 0.0
    err = sum(1 for r in records if r.get("status") == "DEGRADED" or r.get("error", False))
    return round(err / len(records), 4)


@register("tenure_days", "v1")
def _tenure_days(records: list[dict]) -> float:
    """Days since the earliest customer event for the entity."""
    if not records:
        return 0.0
    created = min(_tz(r.get("event_time")) for r in records)
    now = datetime.now(timezone.utc)
    return round((now - created).total_seconds() / 86400.0, 2)
