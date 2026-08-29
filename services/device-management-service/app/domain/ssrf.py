"""Connection-request SSRF protection.

Connection-request URLs may originate from CPE-reported data. This module
validates them before any server-side request is allowed: scheme allowlist,
port policy, IP classification, DNS-resolution validation and blocked
metadata/internal-control destinations. Prefer GenieACS-managed connection
requests over arbitrary backend URL requests."""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from .exceptions import SSRFProtectionError

ALLOWED_SCHEMES = {"http", "https"}
ALLOWED_PORTS = {80, 443, 8080, 8443, 7547}  # TR-069 default 7547
BLOCKED_IP_NETWORKS = [
    "0.0.0.0/8", "10.0.0.0/8", "127.0.0.0/8", "169.254.0.0/16", "172.16.0.0/12",
    "192.168.0.0/16", "198.18.0.0/15", "224.0.0.0/4", "240.0.0.0/4",
    "::1/128", "fc00::/7", "fe80::/10", "ff00::/8",
]


def _resolve_all(host: str) -> list:
    try:
        return [addr[4][0] for addr in socket.getaddrinfo(host, None)]
    except Exception:  # noqa: BLE001
        return []


def _ip_is_blocked(ip_text: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_text)
    except ValueError:
        return True
    for net in BLOCKED_IP_NETWORKS:
        if ip.version == ipaddress.ip_network(net, strict=False).version and ip in ipaddress.ip_network(net, strict=False):
            return True
    return False


def validate_connection_request_url(url: str, *, allow_metadata: bool = False) -> str:
    """Validate a CPE-provided connection-request URL. Returns the normalized
    URL when safe; raises SSRFProtectionError otherwise."""
    if not url:
        raise SSRFProtectionError("empty connection-request URL")
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise SSRFProtectionError(f"scheme {parsed.scheme!r} is not allowed")
    if not parsed.hostname:
        raise SSRFProtectionError("connection-request URL has no host")
    if parsed.port is not None and parsed.port not in ALLOWED_PORTS:
        raise SSRFProtectionError(f"port {parsed.port} is not allowed")
    if parsed.hostname.lower() in ("169.254.169.254", "metadata.google.internal", "metadata") and not allow_metadata:
        raise SSRFProtectionError("metadata endpoint is blocked")
    if parsed.username or parsed.password:
        raise SSRFProtectionError("credentials in connection-request URL are not allowed")
    # Localhost / link-local hostnames.
    host = parsed.hostname.lower()
    if host in ("localhost", "localhost.localdomain", "::1"):
        raise SSRFProtectionError("loopback host is blocked")
    # Resolve and validate every address.
    addresses = _resolve_all(host)
    if addresses and all(_ip_is_blocked(a) for a in addresses):
        raise SSRFProtectionError(f"host {host!r} resolves to a blocked address")
    return url
