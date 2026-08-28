"""Allowlisted RADIUS translation; unknown values never affect policy or replies."""
import ipaddress
import re
from typing import Any

ATTRIBUTE_LIMITS = {"User-Name": 128, "User-Password": 512, "CHAP-Password": 512, "MS-CHAP-Password": 512, "MS-CHAP2-Response": 512, "NAS-IP-Address": 45, "NAS-Identifier": 128, "Called-Station-Id": 128, "Calling-Station-Id": 64, "Service-Type": 32, "Acct-Session-Id": 255, "Acct-Status-Type": 32, "Framed-IP-Address": 45, "Event-Timestamp": 64, "Acct-Terminate-Cause": 128, "Acct-Unique-Session-Id": 255}
NUMERIC = {"Acct-Input-Octets", "Acct-Output-Octets", "Acct-Input-Gigawords", "Acct-Output-Gigawords", "Acct-Session-Time"}
REPLY_ALLOWLIST = {"Mikrotik-Rate-Limit", "Mikrotik-Group", "Mikrotik-Address-List", "Mikrotik-Mark-Id", "Framed-IP-Address", "Framed-IP-Netmask", "Framed-IPv6-Prefix", "Framed-Pool", "Framed-IPv6-Pool", "Framed-Protocol", "Session-Timeout", "Idle-Timeout", "Acct-Interim-Interval", "Simultaneous-Use", "Filter-Id", "Reply-Message", "Tunnel-Type", "Tunnel-Medium-Type", "Tunnel-Private-Group-Id"}
SECRET_KEYS = {"User-Password", "CHAP-Password", "MS-CHAP-Password", "Cleartext-Password", "shared_secret", "password"}

class AttributeValidationError(ValueError): pass

def normalize_username(value: str) -> str:
    value = value.strip().casefold()
    if not value or len(value) > 128: raise AttributeValidationError("invalid username")
    return value

def normalize_mac(value: str) -> str:
    raw = re.sub(r"[^0-9a-fA-F]", "", value)
    if len(raw) != 12: raise AttributeValidationError("invalid MAC address")
    return ":".join(raw[i:i + 2].lower() for i in range(0, 12, 2))

def normalize_attributes(attributes: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if len(attributes) > 64: raise AttributeValidationError("too many attributes")
    accepted, diagnostic = {}, {}
    for key, value in attributes.items():
        if key not in ATTRIBUTE_LIMITS and key not in NUMERIC:
            diagnostic[key] = "[REDACTED]" if key in SECRET_KEYS else str(value)[:512]
            continue
        if key in NUMERIC:
            try: normalized = int(value)
            except (TypeError, ValueError) as error: raise AttributeValidationError(f"invalid numeric attribute: {key}") from error
            if normalized < 0: raise AttributeValidationError(f"invalid numeric attribute: {key}")
            accepted[key] = normalized
        else:
            if not isinstance(value, (str, int)): raise AttributeValidationError(f"invalid attribute type: {key}")
            normalized = str(value).strip()
            if len(normalized) > ATTRIBUTE_LIMITS[key]: raise AttributeValidationError(f"attribute too long: {key}")
            accepted[key] = normalized
    if "User-Name" in accepted: accepted["User-Name"] = normalize_username(accepted["User-Name"])
    if "Calling-Station-Id" in accepted: accepted["Calling-Station-Id"] = normalize_mac(accepted["Calling-Station-Id"])
    for key in ("NAS-IP-Address", "Framed-IP-Address"):
        if key in accepted:
            try: accepted[key] = str(ipaddress.ip_address(accepted[key]))
            except ValueError as error: raise AttributeValidationError(f"invalid IP address: {key}") from error
    return accepted, diagnostic

def safe_reply(attributes: dict[str, Any]) -> dict[str, str | int]:
    return {key: value for key, value in attributes.items() if key in REPLY_ALLOWLIST and isinstance(value, (str, int))}

def traffic_counter(attributes: dict[str, Any], direction: str) -> int:
    return (attributes.get(f"Acct-{direction}-Gigawords", 0) << 32) + attributes.get(f"Acct-{direction}-Octets", 0)
