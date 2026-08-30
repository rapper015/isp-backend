"""Compile a vendor-neutral network policy into RADIUS reply attributes.

MikroTik direction contract: RouterOS `Mikrotik-Rate-Limit` is
`rx-rate[/tx-rate ...]` where rx = receive (download from the client's
perspective) and tx = transmit (upload). The attribute MUST be
`download/upload`; unit tests guard against accidental inversion."""
from __future__ import annotations

from typing import Any

from ..radius import safe_reply

VALID_POLICY_KEYS = {
    "upload_kbps", "download_kbps",
    "upload_min_kbps", "download_min_kbps",
    "burst_upload_kbps", "burst_download_kbps",
    "burst_threshold_bytes", "burst_duration_seconds",
    "priority", "queue_type",
    "static_ipv4", "static_ipv6", "ipv4_pool", "ipv6_pool",
    "framed_protocol", "session_timeout", "idle_timeout",
    "interim_interval", "simultaneous_limit", "filter_id",
    "address_list", "mikrotik_group", "mikrotik_mark_id", "vlan",
    "fup_upload_kbps", "fup_download_kbps",
}


def format_rate_kbps(value: int) -> str:
    if value < 0:
        raise ValueError("rate must be non-negative")
    if value == 0:
        return "0k"
    if value % 1000 == 0:
        return f"{value // 1000}M"
    return f"{value}k"


def format_mikrotik_rate_limit(
    upload_kbps: int | None,
    download_kbps: int | None,
    burst_upload_kbps: int | None = None,
    burst_download_kbps: int | None = None,
    burst_threshold_bytes: int | None = None,
    burst_duration_seconds: int | None = None,
) -> str:
    """RouterOS rate-limit string with rx (download) first, then tx (upload).

    Format: rx[/tx [burst-rx [burst-tx [burst-threshold [burst-time]]]]]
    - burst-rx / burst-tx are kbps rates
    - burst-threshold is bytes
    - burst-time is seconds
    """
    if upload_kbps is None or download_kbps is None:
        raise ValueError("upload_kbps and download_kbps are required")
    base = f"{format_rate_kbps(int(download_kbps))}/{format_rate_kbps(int(upload_kbps))}"
    burst = [burst_download_kbps, burst_upload_kbps, burst_threshold_bytes, burst_duration_seconds]
    if not any(value is not None for value in burst):
        return base
    parts = [base]
    for index, value in enumerate(burst):
        if value is None:
            parts.append("0k")
        elif index >= 2:  # burst-threshold (bytes) and burst-time (seconds)
            parts.append(str(int(value)))
        else:
            parts.append(format_rate_kbps(int(value)))
    return " ".join(parts)


def validate_policy_body(policy: dict) -> list[str]:
    """Return a list of invalid/unknown keys; empty means valid."""
    errors: list[str] = []
    for key in policy:
        if key not in VALID_POLICY_KEYS:
            errors.append(f"unknown policy key {key!r}")
    for key in ("upload_kbps", "download_kbps", "upload_min_kbps", "download_min_kbps", "burst_upload_kbps", "burst_download_kbps", "burst_threshold_bytes", "burst_duration_seconds", "session_timeout", "idle_timeout", "interim_interval", "simultaneous_limit"):
        if key in policy and policy[key] is not None:
            try:
                if int(policy[key]) < 0:
                    errors.append(f"{key} must be non-negative")
            except (TypeError, ValueError):
                errors.append(f"{key} must be an integer")
    return errors


def compile_radius_attributes(policy: dict) -> dict[str, Any]:
    """Vendor-neutral policy -> RADIUS reply attributes (safe, validated)."""
    reply: dict[str, Any] = {}
    upload = policy.get("upload_kbps")
    download = policy.get("download_kbps")
    if upload is not None and download is not None:
        reply["Mikrotik-Rate-Limit"] = format_mikrotik_rate_limit(
            upload_kbps=upload,
            download_kbps=download,
            burst_upload_kbps=policy.get("burst_upload_kbps"),
            burst_download_kbps=policy.get("burst_download_kbps"),
            burst_threshold_bytes=policy.get("burst_threshold_bytes"),
            burst_duration_seconds=policy.get("burst_duration_seconds"),
        )
    mappings = {
        "static_ipv4": "Framed-IP-Address",
        "static_ipv6": "Framed-IPv6-Prefix",
        "ipv4_pool": "Framed-Pool",
        "ipv6_pool": "Framed-IPv6-Pool",
        "framed_protocol": "Framed-Protocol",
        "session_timeout": "Session-Timeout",
        "idle_timeout": "Idle-Timeout",
        "interim_interval": "Acct-Interim-Interval",
        "simultaneous_limit": "Simultaneous-Use",
        "filter_id": "Filter-Id",
        "address_list": "Mikrotik-Address-List",
        "mikrotik_group": "Mikrotik-Group",
        "mikrotik_mark_id": "Mikrotik-Mark-Id",
        "vlan": "Tunnel-Private-Group-Id",
    }
    for source, target in mappings.items():
        if policy.get(source) is not None:
            reply[target] = policy[source]
    return safe_reply(reply)
