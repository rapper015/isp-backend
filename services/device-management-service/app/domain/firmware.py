"""Firmware security and rollout rules: checksum validation, compatibility,
canary/phased rollout thresholds and capability-aware rollback claims."""
from __future__ import annotations

from ..enums import ROLLBACK_CAPABILITIES


def validate_checksum(data: bytes, expected_sha256: str) -> bool:
    import hashlib

    return hashlib.sha256(data).hexdigest().lower() == expected_sha256.lower()


def version_in_range(current: str | None, minimum: str | None, maximum: str | None) -> bool:
    """Simple dotted-version range check. Treats unknown as not matching a range."""
    if not current:
        return minimum is None and maximum is None
    if minimum and _compare_versions(current, minimum) < 0:
        return False
    if maximum and _compare_versions(current, maximum) > 0:
        return False
    return True


def _compare_versions(a: str, b: str) -> int:
    def parts(v: str):
        return [int(x) if x.isdigit() else x for x in _split_version(v)]

    pa, pb = parts(a), parts(b)
    for x, y in zip(pa, pb):
        if isinstance(x, int) and isinstance(y, int):
            if x != y:
                return -1 if x < y else 1
        else:
            xs, ys = str(x), str(y)
            if xs != ys:
                return -1 if xs < ys else 1
    return (len(pa) > len(pb)) - (len(pa) < len(pb))


def _split_version(v: str):
    import re

    return re.split(r"[.\-_]", v)


def canary_passes(successes: int, failures: int, *, success_threshold: float, failure_threshold: float,
                  minimum_sample: int = 1) -> str:
    """Return 'CONTINUE', 'COMPLETE' or 'PAUSE' for a stage given outcomes."""
    total = successes + failures
    if failures and failures / total >= failure_threshold:
        return "PAUSE"
    if total < minimum_sample:
        return "CONTINUE"
    if successes / total >= success_threshold:
        return "COMPLETE"
    return "CONTINUE"


def rollback_claim_supported(rollback_capability: str) -> bool:
    """Only devices that genuinely support rollback may claim it."""
    return rollback_capability in ("DUAL_BANK", "VENDOR_DOWNGRADE", "AUTOMATIC_BOOT_ROLLBACK")


def validate_rollout_policy(policy: dict) -> list[str]:
    errors = []
    for key in ("max_concurrent_downloads", "max_concurrent_reboots", "stage_size"):
        value = policy.get(key)
        if value is not None and int(value) <= 0:
            errors.append(f"{key} must be positive")
    if policy.get("success_threshold") is not None and not (0 < float(policy["success_threshold"]) <= 1):
        errors.append("success_threshold must be in (0, 1]")
    if policy.get("failure_threshold") is not None and not (0 < float(policy["failure_threshold"]) <= 1):
        errors.append("failure_threshold must be in (0, 1]")
    return errors


def compute_stage_size(strategy: str, fleet_size: int, stage_number: int, stage_percentage: int | None) -> int:
    if stage_percentage is not None:
        return max(1, int(fleet_size * stage_percentage / 100))
    # Simple geometric canary: 1, 2, 4, ...
    return min(fleet_size, 2 ** max(0, stage_number - 1))
