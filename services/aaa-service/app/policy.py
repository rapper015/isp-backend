"""Deterministic policy precedence and safe RADIUS reply rendering."""
from dataclasses import dataclass, field
from typing import Any
from .radius import safe_reply
from .network_control.radius_compiler import format_mikrotik_rate_limit

PRECEDENCE = ("platform", "tenant", "nas", "plan", "subscription", "subscriber", "temporary", "quota")

@dataclass(frozen=True)
class EffectivePolicy:
    values: dict[str, Any]
    provenance: dict[str, str]
    def reply_attributes(self) -> dict[str, str | int]:
        value = self.values
        reply = dict(value.get("reply_attributes", {}))
        upload, download = value.get("upload_kbps"), value.get("download_kbps")
        if upload is not None and download is not None:
            reply["Mikrotik-Rate-Limit"] = format_mikrotik_rate_limit(
                upload_kbps=upload,
                download_kbps=download,
                burst_upload_kbps=value.get("burst_upload_kbps"),
                burst_download_kbps=value.get("burst_download_kbps"),
                burst_threshold_bytes=value.get("burst_threshold_bytes"),
                burst_duration_seconds=value.get("burst_duration_seconds"),
            )
        mappings = {"static_ipv4": "Framed-IP-Address", "static_ipv6": "Framed-IPv6-Prefix", "ipv4_pool": "Framed-Pool", "ipv6_pool": "Framed-IPv6-Pool", "framed_protocol": "Framed-Protocol", "session_timeout": "Session-Timeout", "idle_timeout": "Idle-Timeout", "interim_interval": "Acct-Interim-Interval", "simultaneous_limit": "Simultaneous-Use", "filter_id": "Filter-Id", "address_list": "Mikrotik-Address-List", "mikrotik_group": "Mikrotik-Group", "mikrotik_mark_id": "Mikrotik-Mark-Id", "vlan": "Tunnel-Private-Group-Id"}
        for source, target in mappings.items():
            if value.get(source) is not None: reply[target] = value[source]
        return safe_reply(reply)

def calculate_policy(layers: dict[str, dict[str, Any]]) -> EffectivePolicy:
    values, provenance = {}, {}
    for layer in PRECEDENCE:
        for key, value in layers.get(layer, {}).items():
            if value is not None: values[key], provenance[key] = value, layer
    return EffectivePolicy(values, provenance)
