"""Deterministic policy precedence and safe RADIUS reply rendering."""
from dataclasses import dataclass, field
from typing import Any
from .radius import safe_reply

PRECEDENCE = ("platform", "tenant", "nas", "plan", "subscription", "subscriber", "temporary", "quota")

@dataclass(frozen=True)
class EffectivePolicy:
    values: dict[str, Any]
    provenance: dict[str, str]
    def reply_attributes(self) -> dict[str, str | int]:
        value = self.values
        reply = dict(value.get("reply_attributes", {}))
        upload, download = value.get("upload_kbps"), value.get("download_kbps")
        if upload is not None and download is not None: reply["Mikrotik-Rate-Limit"] = f"{int(upload)}k/{int(download)}k"
        mappings = {"static_ipv4": "Framed-IP-Address", "ipv4_pool": "Framed-Pool", "ipv6_pool": "Framed-IPv6-Pool", "session_timeout": "Session-Timeout", "idle_timeout": "Idle-Timeout", "interim_interval": "Acct-Interim-Interval", "simultaneous_limit": "Simultaneous-Use", "filter_id": "Filter-Id", "address_list": "Mikrotik-Address-List", "vlan": "Tunnel-Private-Group-Id"}
        for source, target in mappings.items():
            if value.get(source) is not None: reply[target] = value[source]
        return safe_reply(reply)

def calculate_policy(layers: dict[str, dict[str, Any]]) -> EffectivePolicy:
    values, provenance = {}, {}
    for layer in PRECEDENCE:
        for key, value in layers.get(layer, {}).items():
            if value is not None: values[key], provenance[key] = value, layer
    return EffectivePolicy(values, provenance)
