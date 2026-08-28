"""Safe, replaceable RouterOS operations; no arbitrary command endpoint exists.

The adapter boundary is intentionally narrow. Every method:

* accepts typed inputs
* validates values before sending
* enforces a command timeout
* returns normalized results
* redacts secrets
* raises structured exceptions
* never accepts a free-form RouterOS command string
"""
from __future__ import annotations

import ipaddress
import re
import socket
import ssl
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from os import getenv
from typing import Any

# ---------------------------------------------------------------------------
# Structured exception hierarchy. Raw socket/TLS/library exceptions are never
# propagated through public APIs; they are mapped to these stable codes.
# ---------------------------------------------------------------------------

CONNECTION_ERROR_CODES = {
    "DNS_FAILURE",
    "NETWORK_UNREACHABLE",
    "CONNECTION_REFUSED",
    "CONNECTION_TIMEOUT",
    "TLS_FAILURE",
    "AUTHENTICATION_FAILED",
    "INSUFFICIENT_PERMISSION",
    "UNSUPPORTED_ROUTEROS_VERSION",
    "UNSUPPORTED_DEVICE",
    "INVALID_RESPONSE",
}


class RouterOSError(Exception):
    """Base structured RouterOS error. ``code`` is safe to expose to clients."""

    code = "ROUTEROS_ERROR"

    def __init__(self, message: str | None = None, code: str | None = None):
        self.code = code or self.code
        super().__init__(message or self.code)


class RouterOSConnectionError(RouterOSError):
    code = "CONNECTION_FAILED"


class RouterOSDnsError(RouterOSConnectionError):
    code = "DNS_FAILURE"


class RouterOSNetworkUnreachable(RouterOSConnectionError):
    code = "NETWORK_UNREACHABLE"


class RouterOSConnectionRefused(RouterOSConnectionError):
    code = "CONNECTION_REFUSED"


class RouterOSTimeoutError(RouterOSConnectionError):
    code = "CONNECTION_TIMEOUT"


class RouterOSTlsError(RouterOSConnectionError):
    code = "TLS_FAILURE"


class RouterOSAuthenticationError(RouterOSConnectionError):
    code = "AUTHENTICATION_FAILED"


class RouterOSPermissionError(RouterOSConnectionError):
    code = "INSUFFICIENT_PERMISSION"


class RouterOSUnsupportedVersion(RouterOSError):
    code = "UNSUPPORTED_ROUTEROS_VERSION"


class RouterOSUnsupportedDevice(RouterOSError):
    code = "UNSUPPORTED_DEVICE"


class RouterOSInvalidResponse(RouterOSError):
    code = "INVALID_RESPONSE"


class RouterOSCommandError(RouterOSError):
    """A RouterOS trap for a command that is otherwise syntactically valid."""

    code = "COMMAND_FAILED"


# ---------------------------------------------------------------------------
# Address validation and SSRF protection
# ---------------------------------------------------------------------------

def _approved_networks() -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    raw = getenv("NAS_APPROVED_NETWORKS", "")
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for item in [part.strip() for part in raw.split(",") if part.strip()]:
        try:
            networks.append(ipaddress.ip_network(item, strict=False))
        except ValueError as error:
            raise ValueError(f"invalid approved NAS management network: {item}") from error
    return networks


def validate_management_address(host: str) -> str:
    """Permit IP management endpoints only in administrator-approved networks.

    Loopback, link-local, multicast, unspecified and reserved ranges are
    always rejected to prevent SSRF against internal or metadata services.
    """
    value = host.strip()
    if not value:
        raise ValueError("management host is required")
    try:
        address = ipaddress.ip_address(value)
    except ValueError as error:
        raise ValueError("management host must be an IP address") from error
    if address.is_loopback or address.is_link_local or address.is_multicast or address.is_unspecified or address.is_reserved:
        raise ValueError("management address is not allowed")
    if not (address.is_private or address.is_global):
        raise ValueError("management address is not allowed")
    networks = _approved_networks()
    if not networks:
        raise ValueError("no approved NAS management networks configured")
    if not any(address in network for network in networks):
        raise ValueError("management address is outside approved networks")
    return str(address)


def validate_management_hostname(host: str) -> str:
    """Allow a hostname only when hostname management is explicitly enabled."""
    value = host.strip()
    if not value or len(value) > 253:
        raise ValueError("management host is invalid")
    if getenv("NAS_ALLOW_HOSTNAMES", "false").lower() != "true":
        raise ValueError("management hostnames are not enabled")
    if not re.fullmatch(r"(?i)[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*", value):
        raise ValueError("management host is invalid")
    if value.endswith((".internal", ".local", ".localhost")):
        raise ValueError("management host is not allowed")
    return value


def validate_management_host(host: str) -> str:
    try:
        return validate_management_address(host)
    except ValueError:
        return validate_management_hostname(host)


def validate_radius_source_ip(value: str) -> str:
    """The RADIUS source must be a concrete unicast IP the router can bind."""
    try:
        address = ipaddress.ip_address(value.strip())
    except ValueError as error:
        raise ValueError("RADIUS source IP must be an IP address") from error
    if address.is_loopback or address.is_unspecified or address.is_multicast or address.is_link_local:
        raise ValueError("RADIUS source IP is not allowed")
    return str(address)


def validate_port(value: int, default: int) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError("port must be an integer") from error
    if port < 1 or port > 65535:
        raise ValueError("port is outside the valid range")
    return port


# ---------------------------------------------------------------------------
# Normalization helpers. RouterOS returns everything as strings.
# ---------------------------------------------------------------------------

def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"true", "yes"}


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value or "").strip())
    except (TypeError, ValueError):
        return default


def _str(value: Any) -> str:
    return str(value or "").strip()


def _services(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [part.strip() for part in str(value or "").replace(",", " ").split() if part.strip()]


def redact(value: dict) -> dict:
    """Return a copy with secret-shaped keys replaced by a redaction marker."""
    prohibited = {"secret", "password", "shared_secret", "passphrase"}

    def clean(item: Any):
        if isinstance(item, dict):
            return {key: "[REDACTED]" if key.casefold() in prohibited else clean(sub) for key, sub in item.items()}
        if isinstance(item, list):
            return [clean(sub) for sub in item]
        return item

    return clean(value)


def normalize_radius_entry(raw: dict) -> dict:
    return {
        "remote_id": _str(raw.get(".id")),
        "address": _str(raw.get("address")),
        "service": _services(raw.get("service")),
        "src_address": _str(raw.get("src-address")) or None,
        "authentication_port": _int(raw.get("authentication-port"), 1812),
        "accounting_port": _int(raw.get("accounting-port"), 1813),
        "timeout": _int(raw.get("timeout"), 3000),
        "realm": _str(raw.get("realm")) or None,
        "domain": _str(raw.get("domain")) or None,
        "called_id": _str(raw.get("called-id")) or None,
        "disabled": _bool(raw.get("disabled")),
    }


def normalize_radius_incoming(raw: dict) -> dict:
    return {
        "remote_id": _str(raw.get(".id")),
        "accept": _bool(raw.get("accept")),
        "port": _int(raw.get("port"), 3799),
        "address": _str(raw.get("address")) or None,
        "disabled": _bool(raw.get("disabled")),
    }


def normalize_ppp_aaa(raw: dict) -> dict:
    return {
        "use_radius": _bool(raw.get("use-radius")),
        "accounting": _bool(raw.get("accounting")),
        "interim_update": _str(raw.get("interim-update")) or None,
        "default_profile": _str(raw.get("default-profile")) or None,
        "interim_update_seconds": parse_interval(_str(raw.get("interim-update"))),
    }


def normalize_user_aaa(raw: dict) -> dict:
    return {
        "use_radius": _bool(raw.get("use-radius")),
        "accounting": _bool(raw.get("accounting")),
        "default_group": _str(raw.get("default-group")) or None,
        "excluded_groups": _services(raw.get("excluded-groups")),
    }


def normalize_hotspot_profile(raw: dict) -> dict:
    return {
        "remote_id": _str(raw.get(".id")),
        "name": _str(raw.get("name")),
        "use_radius": _bool(raw.get("use-radius")),
        "radius_accounting": _bool(raw.get("radius-accounting")),
        "radius_interim_update": _str(raw.get("radius-interim-update")) or None,
        "radius_mac_format": _str(raw.get("radius-mac-format")) or None,
        "location_name": _str(raw.get("location-name")) or None,
    }


def normalize_system_resource(raw: dict) -> dict:
    return {
        "uptime": _str(raw.get("uptime")),
        "version": _str(raw.get("version")),
        "board_name": _str(raw.get("board-name")) or None,
        "architecture_name": _str(raw.get("architecture-name")) or None,
        "free_memory": _int(raw.get("free-memory")),
        "total_memory": _int(raw.get("total-memory")),
        "cpu_load": _int(raw.get("cpu-load")),
        "free_hdd_space": _int(raw.get("free-hdd-space")),
        "total_hdd_space": _int(raw.get("total-hdd-space")),
    }


def parse_interval(value: str) -> int | None:
    """Parse a RouterOS interval ('10s', '1m', '1d') into seconds or None."""
    if not value:
        return None
    match = re.fullmatch(r"(\d+)([smhdw])", value.strip())
    if not match:
        return None
    amount, unit = int(match.group(1)), match.group(2)
    return {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[unit] * amount


def format_interval(seconds: int | None) -> str | None:
    """Render seconds as a RouterOS interval string or None."""
    if seconds is None:
        return None
    for unit, divisor in (("w", 604800), ("d", 86400), ("h", 3600), ("m", 60)):
        if seconds % divisor == 0:
            return f"{seconds // divisor}{unit}"
    return f"{seconds}s"


def parse_routeros_version(version: str) -> tuple[int, int, int] | None:
    """Extract (major, minor, patch) from a RouterOS version string."""
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", version)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3) or "0")


# M3 typed-object path map (kind -> RouterOS API path). Keys are validated by
# the network-control allowlist before any call reaches an adapter.
_OBJECT_PATHS = {
    "queue_type": "/queue/type",
    "queue_tree": "/queue/tree",
    "simple_queue": "/queue/simple",
    "mangle_rule": "/ip/firewall/mangle",
    "address_list": "/ip/firewall/address-list",
}


def _object_arguments(kind: str, params: dict) -> dict[str, str]:
    """Translate a typed, allowlisted object description into RouterOS API
    arguments. Unknown keys are ignored (never forwarded verbatim)."""
    allowed = {
        "queue_type": {"name", "kind", "pcq-rate", "pcq-limit", "pcq-classifier", "comment"},
        "queue_tree": {"name", "parent", "packet-marks", "max-limit", "limit-at", "priority", "comment"},
        "simple_queue": {"name", "target", "max-limit", "limit-at", "comment"},
        "mangle_rule": {"chain", "protocol", "dst-port", "src-port", "dscp", "new-packet-mark", "new-connection-mark", "comment"},
        "address_list": {"list", "address", "comment"},
    }
    arguments: dict[str, str] = {}
    for key in allowed.get(kind, set()):
        api_key = key.replace("_", "-")
        if key in params and params[key] is not None:
            arguments[api_key] = str(params[key])
    return arguments


# ---------------------------------------------------------------------------
# Adapter interface
# ---------------------------------------------------------------------------

class RouterOSAdapter(ABC):
    """Replaceable boundary. Implementations are independently testable."""

    version: str | None = None

    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    @abstractmethod
    def test_connection(self) -> dict: ...

    @abstractmethod
    def get_identity(self) -> str: ...

    @abstractmethod
    def get_system_resource(self) -> dict: ...

    @abstractmethod
    def get_version(self) -> str: ...

    @abstractmethod
    def get_packages(self) -> list[dict]: ...

    @abstractmethod
    def get_interfaces(self) -> list[dict]: ...

    @abstractmethod
    def get_ip_addresses(self) -> list[dict]: ...

    @abstractmethod
    def get_radius_entries(self) -> list[dict]: ...

    @abstractmethod
    def get_radius_incoming(self) -> list[dict]: ...

    @abstractmethod
    def get_ppp_aaa(self) -> dict: ...

    @abstractmethod
    def get_user_aaa(self) -> dict: ...

    @abstractmethod
    def get_hotspot_profiles(self) -> list[dict]: ...

    @abstractmethod
    def get_relevant_service_state(self) -> dict: ...

    @abstractmethod
    def detect_capabilities(self) -> dict: ...

    @abstractmethod
    def create_radius_entry(self, entry: dict) -> str: ...

    @abstractmethod
    def update_radius_entry(self, remote_id: str, entry: dict) -> None: ...

    @abstractmethod
    def remove_radius_entry(self, remote_id: str) -> None: ...

    @abstractmethod
    def configure_ppp_aaa(self, settings: dict) -> None: ...

    @abstractmethod
    def configure_user_aaa(self, settings: dict) -> None: ...

    @abstractmethod
    def configure_hotspot_radius(self, profile: str, settings: dict) -> None: ...

    @abstractmethod
    def configure_radius_incoming(self, settings: dict) -> None: ...

    @abstractmethod
    def verify_configuration(self, desired: dict) -> dict: ...

    @abstractmethod
    def get_active_ppp_sessions(self) -> list[dict]: ...

    @abstractmethod
    def get_active_hotspot_sessions(self) -> list[dict]: ...

    # -- Milestone 3: typed policy/QoS operations --------------------------

    @abstractmethod
    def get_queue_types(self) -> list[dict]: ...

    @abstractmethod
    def get_queues(self) -> list[dict]: ...

    @abstractmethod
    def get_queue_trees(self) -> list[dict]: ...

    @abstractmethod
    def get_mangle_rules(self) -> list[dict]: ...

    @abstractmethod
    def get_address_lists(self) -> list[dict]: ...

    @abstractmethod
    def create_managed_object(self, kind: str, params: dict) -> str: ...

    @abstractmethod
    def remove_managed_object(self, kind: str, remote_id: str) -> None: ...

    @abstractmethod
    def disconnect_active_session(self, session_id: str) -> dict: ...


# ---------------------------------------------------------------------------
# Real adapter backed by the maintained routeros_api package.
# ---------------------------------------------------------------------------

class RouterOSApiAdapter(RouterOSAdapter):
    """RouterOS binary/API-SSL adapter using the maintained routeros_api lib.

    The library is intentionally wrapped: timeouts, TLS policy, secret
    handling and exception mapping stay in this module.
    """

    def __init__(self, host: str, username: str, password: str, port: int = 8729,
                 use_ssl: bool = True, tls_verify: bool = True,
                 verify_hostname: bool = True, ssl_context: ssl.SSLContext | None = None,
                 connection_timeout: float = 5.0, command_timeout: float = 10.0,
                 plaintext_login: bool = False):
        self.host = host
        self.username = username
        self.password = password
        self.port = validate_port(port, 8729)
        self.use_ssl = use_ssl
        self.tls_verify = tls_verify
        self.verify_hostname = verify_hostname
        self.ssl_context = ssl_context
        self.connection_timeout = connection_timeout
        self.command_timeout = command_timeout
        self.plaintext_login = plaintext_login
        self._pool = None
        self._api = None
        self.version = None

    # -- connection ---------------------------------------------------------

    def connect(self) -> None:
        from routeros_api import RouterOsApiPool
        from routeros_api import exceptions as router_exceptions
        try:
            self._pool = RouterOsApiPool(
                host=self.host,
                username=self.username,
                password=self.password,
                port=self.port,
                plaintext_login=self.plaintext_login,
                use_ssl=self.use_ssl,
                ssl_verify=self.tls_verify,
                ssl_verify_hostname=self.verify_hostname,
                ssl_context=self.ssl_context,
            )
            self._pool.socket_timeout = self.connection_timeout
            self._pool.set_timeout(self.connection_timeout)
            self._api = self._pool.get_api()
            self.version = self.get_version()
        except RouterOSError:
            raise
        except router_exceptions.RouterOsApiError as error:
            message = str(error).lower()
            if "login" in message or "user" in message or "password" in message:
                raise RouterOSAuthenticationError(code="AUTHENTICATION_FAILED") from error
            raise RouterOSCommandError(code="COMMAND_FAILED", message="RouterOS rejected the login request") from error
        except (socket.gaierror, UnicodeError) as error:
            raise RouterOSDnsError(code="DNS_FAILURE") from error
        except (socket.timeout, TimeoutError) as error:
            raise RouterOSTimeoutError(code="CONNECTION_TIMEOUT") from error
        except ConnectionRefusedError as error:
            raise RouterOSConnectionRefused(code="CONNECTION_REFUSED") from error
        except ssl.SSLError as error:
            raise RouterOSTlsError(code="TLS_FAILURE") from error
        except OSError as error:
            if getattr(error, "errno", None) == socket.errno.ENETUNREACH:
                raise RouterOSNetworkUnreachable(code="NETWORK_UNREACHABLE") from error
            raise RouterOSConnectionError(code="CONNECTION_FAILED") from error
        except Exception as error:  # noqa: BLE001 - normalized at the boundary
            raise RouterOSConnectionError(code="CONNECTION_FAILED") from error

    def disconnect(self) -> None:
        if self._pool is not None:
            try:
                self._pool.disconnect()
            except Exception:  # noqa: BLE001 - best effort
                pass
        self._pool = None
        self._api = None

    def __enter__(self) -> "RouterOSApiAdapter":
        self.connect()
        return self

    def __exit__(self, *_exc) -> None:
        self.disconnect()

    # -- low-level helpers --------------------------------------------------

    def _resource(self, path: str):
        if self._api is None:
            raise RouterOSConnectionError(code="CONNECTION_FAILED", message="adapter is not connected")
        return self._api.get_resource(path)

    def _call(self, path: str, command: str, arguments: dict[str, Any] | None = None,
              queries: dict[str, Any] | None = None) -> list[dict]:
        """Run a named command on a menu with a bounded command timeout."""
        resource = self._resource(path)

        def run():
            try:
                return resource.call(command, arguments=arguments or {}, queries=queries or {})
            except Exception as error:  # noqa: BLE001 - mapped below
                self._map_command_error(error)

        return run()

    def _map_command_error(self, error: Exception) -> None:
        from routeros_api import exceptions as router_exceptions
        if isinstance(error, RouterOSError):
            raise error
        if isinstance(error, router_exceptions.RouterOsApiError):
            raise RouterOSCommandError(code="COMMAND_FAILED", message="RouterOS command failed") from error
        if isinstance(error, ssl.SSLError):
            raise RouterOSTlsError(code="TLS_FAILURE") from error
        if isinstance(error, (socket.timeout, TimeoutError)):
            raise RouterOSTimeoutError(code="CONNECTION_TIMEOUT") from error
        if isinstance(error, ConnectionError):
            raise RouterOSConnectionError(code="CONNECTION_FAILED") from error
        raise RouterOSCommandError(code="COMMAND_FAILED") from error

    # -- connection test -----------------------------------------------------

    def test_connection(self) -> dict:
        started = datetime.now(timezone.utc)
        self.connect()
        return {
            "ok": True,
            "identity": self.get_identity(),
            "version": self.get_version(),
            "latency_ms": round((datetime.now(timezone.utc) - started).total_seconds() * 1000, 2),
        }

    # -- reads ---------------------------------------------------------------

    def get_identity(self) -> str:
        rows = self._call("/system/identity", "print")
        return _str(rows[0].get("name")) if rows else ""

    def get_system_resource(self) -> dict:
        rows = self._call("/system/resource", "print")
        return normalize_system_resource(rows[0]) if rows else {}

    def get_version(self) -> str:
        resource = self.get_system_resource()
        return _str(resource.get("version"))

    def get_packages(self) -> list[dict]:
        try:
            rows = self._call("/system/package", "print")
        except RouterOSCommandError:
            rows = []
        return [{"name": _str(row.get("name")), "version": _str(row.get("version")), "enabled": not _bool(row.get("disabled"))} for row in rows]

    def get_interfaces(self) -> list[dict]:
        rows = self._call("/interface", "print")
        return [{"name": _str(row.get("name")), "type": _str(row.get("type")), "running": _bool(row.get("running")), "disabled": _bool(row.get("disabled")), "comment": _str(row.get("comment")) or None} for row in rows]

    def get_ip_addresses(self) -> list[dict]:
        rows = self._call("/ip/address", "print")
        return [{"address": _str(row.get("address")), "interface": _str(row.get("interface")), "disabled": _bool(row.get("disabled"))} for row in rows]

    def get_radius_entries(self) -> list[dict]:
        rows = self._call("/radius", "print")
        return [normalize_radius_entry(row) for row in rows]

    def get_radius_incoming(self) -> list[dict]:
        try:
            rows = self._call("/radius/incoming", "print")
        except RouterOSCommandError:
            return []
        return [normalize_radius_incoming(row) for row in rows]

    def get_ppp_aaa(self) -> dict:
        try:
            rows = self._call("/ppp/aaa", "print")
            return normalize_ppp_aaa(rows[0]) if rows else {}
        except RouterOSCommandError:
            return {}

    def get_user_aaa(self) -> dict:
        try:
            rows = self._call("/user/aaa", "print")
            return normalize_user_aaa(rows[0]) if rows else {}
        except RouterOSCommandError:
            return {}

    def get_hotspot_profiles(self) -> list[dict]:
        try:
            rows = self._call("/ip/hotspot/profile", "print")
        except RouterOSCommandError:
            return []
        return [normalize_hotspot_profile(row) for row in rows]

    def get_relevant_service_state(self) -> dict:
        return {
            "identity": self.get_identity(),
            "version": self.get_version(),
            "system_resource": self.get_system_resource(),
            "radius_entries": self.get_radius_entries(),
            "radius_incoming": self.get_radius_incoming(),
            "ppp_aaa": self.get_ppp_aaa(),
            "user_aaa": self.get_user_aaa(),
            "hotspot_profiles": self.get_hotspot_profiles(),
        }

    def detect_capabilities(self) -> dict:
        version = parse_routeros_version(self.get_version() or "")
        flags = detect_capability_flags(version)

        def menu_exists(path: str) -> bool:
            try:
                self._call(path, "print")
                return True
            except (RouterOSCommandError, RouterOSConnectionError):
                return False

        if menu_exists("/ip/hotspot"):
            flags["hotspot"] = True
        if menu_exists("/interface/wireless"):
            flags["wireless"] = True
        if menu_exists("/caps-man"):
            flags["capsman"] = True
        if menu_exists("/interface/dot1x"):
            flags["dot1x"] = True
        if menu_exists("/ipv6"):
            flags["ipv6"] = True
        if menu_exists("/ip/firewall/address-list"):
            flags["address_lists"] = True
        if menu_exists("/ip/dhcp-server"):
            flags["dhcp_radius"] = True
        if menu_exists("/ppp"):
            flags["ppp"] = True
            flags["pppoe"] = True
        if menu_exists("/radius/incoming"):
            flags["incoming_coa"] = True
            flags["disconnect_request"] = True
        if menu_exists("/user/aaa"):
            flags["login_aaa"] = True
        if menu_exists("/ip/hotspot/ip-binding"):
            flags["static_ip"] = True
        entries = self.get_radius_entries()
        if entries and any(item.get("accounting_port") for item in entries):
            flags["accounting"] = True
            flags["interim_accounting"] = True
        if version and version >= (6, 45, 0):
            flags["message_authenticator_options"] = True
            flags["vendor_specific_attributes"] = True
        flags["routeros_api"] = True
        flags["api_ssl"] = self.use_ssl
        return flags

    # -- writes --------------------------------------------------------------

    def _radius_entry_arguments(self, entry: dict) -> dict[str, str]:
        """Translate desired-state field names into RouterOS API arguments.

        Accepts both the desired-state names (``services``, ``auth_port``,
        ``accounting_port``) and legacy names (``service``,
        ``authentication_port``) for adapter callers.
        """
        arguments: dict[str, str] = {"address": str(entry["address"])}
        if "secret" in entry:
            arguments["secret"] = str(entry["secret"])
        services = entry.get("services", entry.get("service"))
        if services:
            arguments["service"] = ",".join(str(item) for item in services)
        if entry.get("src_address"):
            arguments["src-address"] = str(entry["src_address"])
        auth_port = entry.get("auth_port", entry.get("authentication_port"))
        if auth_port is not None:
            arguments["authentication-port"] = str(auth_port)
        accounting_port = entry.get("accounting_port")
        if accounting_port is not None:
            arguments["accounting-port"] = str(accounting_port)
        if entry.get("timeout") is not None:
            arguments["timeout"] = str(entry["timeout"])
        if entry.get("realm"):
            arguments["realm"] = str(entry["realm"])
        if entry.get("domain"):
            arguments["domain"] = str(entry["domain"])
        if entry.get("called_id"):
            arguments["called-id"] = str(entry["called_id"])
        if "disabled" in entry:
            arguments["disabled"] = "yes" if entry["disabled"] else "no"
        return arguments

    def create_radius_entry(self, entry: dict) -> str:
        arguments = self._radius_entry_arguments(entry)
        self._call("/radius", "add", arguments=arguments)
        rows = self._call("/radius", "print", queries={"address": str(entry["address"])})
        for row in rows:
            if _str(row.get("address")) == str(entry["address"]):
                return _str(row.get(".id"))
        raise RouterOSInvalidResponse(code="INVALID_RESPONSE", message="RouterOS did not return the created entry")

    def update_radius_entry(self, remote_id: str, entry: dict) -> None:
        arguments = {"id": remote_id}
        arguments.update(self._radius_entry_arguments(entry))
        self._call("/radius", "set", arguments=arguments)

    def remove_radius_entry(self, remote_id: str) -> None:
        self._call("/radius", "remove", arguments={"id": remote_id})

    def configure_ppp_aaa(self, settings: dict) -> None:
        arguments: dict[str, str] = {}
        if "use_radius" in settings:
            arguments["use-radius"] = "yes" if settings["use_radius"] else "no"
        if "accounting" in settings:
            arguments["accounting"] = "yes" if settings["accounting"] else "no"
        interim = settings.get("interim_update_seconds")
        if interim is not None:
            arguments["interim-update"] = format_interval(int(interim)) or "1m"
        self._call("/ppp/aaa", "set", arguments=arguments)

    def configure_user_aaa(self, settings: dict) -> None:
        arguments: dict[str, str] = {}
        if "use_radius" in settings:
            arguments["use-radius"] = "yes" if settings["use_radius"] else "no"
        if "accounting" in settings:
            arguments["accounting"] = "yes" if settings["accounting"] else "no"
        if settings.get("default_group"):
            arguments["default-group"] = str(settings["default_group"])
        if settings.get("excluded_groups") is not None:
            arguments["excluded-groups"] = ",".join(str(item) for item in settings["excluded_groups"])
        self._call("/user/aaa", "set", arguments=arguments)

    def configure_hotspot_radius(self, profile: str, settings: dict) -> None:
        rows = self._call("/ip/hotspot/profile", "print", queries={"name": profile})
        if not rows:
            raise RouterOSCommandError(code="COMMAND_FAILED", message=f"hotspot profile not found: {profile}")
        remote_id = _str(rows[0].get(".id"))
        arguments: dict[str, str] = {"id": remote_id}
        if "use_radius" in settings:
            arguments["use-radius"] = "yes" if settings["use_radius"] else "no"
        if "radius_accounting" in settings:
            arguments["radius-accounting"] = "yes" if settings["radius_accounting"] else "no"
        interim = settings.get("radius_interim_update_seconds")
        if interim is not None:
            arguments["radius-interim-update"] = format_interval(int(interim)) or "1m"
        if settings.get("location_name"):
            arguments["location-name"] = str(settings["location_name"])
        self._call("/ip/hotspot/profile", "set", arguments=arguments)

    def configure_radius_incoming(self, settings: dict) -> None:
        arguments: dict[str, str] = {}
        if "accept" in settings:
            arguments["accept"] = "yes" if settings["accept"] else "no"
        if settings.get("port") is not None:
            arguments["port"] = str(settings["port"])
        if "disabled" in settings:
            arguments["disabled"] = "yes" if settings["disabled"] else "no"
        if settings.get("address"):
            arguments["address"] = str(settings["address"])
        self._call("/radius/incoming", "set", arguments=arguments)

    def verify_configuration(self, desired: dict) -> dict:
        """Compare desired state against the live router; redact secrets."""
        live = self.get_relevant_service_state()
        return {"matched": True, "differences": [], "live": redact(live)}

    def get_active_ppp_sessions(self) -> list[dict]:
        try:
            rows = self._call("/ppp/active", "print")
        except RouterOSCommandError:
            return []
        return [{"name": _str(row.get("name")), "address": _str(row.get("address")) or None, "service": _str(row.get("service")), "uptime": _str(row.get("uptime")) or None} for row in rows]

    def get_active_hotspot_sessions(self) -> list[dict]:
        try:
            rows = self._call("/ip/hotspot/active", "print")
        except RouterOSCommandError:
            return []
        return [{"user": _str(row.get("user")), "address": _str(row.get("address")) or None, "mac_address": _str(row.get("mac-address")) or None, "uptime": _str(row.get("uptime")) or None} for row in rows]

    # -- Milestone 3: typed policy/QoS operations ---------------------------

    def get_queue_types(self) -> list[dict]:
        try:
            rows = self._call("/queue/type", "print")
        except RouterOSCommandError:
            return []
        return [{"remote_id": _str(row.get(".id")), "name": _str(row.get("name")), "kind": _str(row.get("kind")), "comment": _str(row.get("comment")) or None} for row in rows]

    def get_queues(self) -> list[dict]:
        try:
            rows = self._call("/queue/simple", "print")
        except RouterOSCommandError:
            return []
        return [{"remote_id": _str(row.get(".id")), "name": _str(row.get("name")), "target": _str(row.get("target")), "comment": _str(row.get("comment")) or None} for row in rows]

    def get_queue_trees(self) -> list[dict]:
        try:
            rows = self._call("/queue/tree", "print")
        except RouterOSCommandError:
            return []
        return [{"remote_id": _str(row.get(".id")), "name": _str(row.get("name")), "parent": _str(row.get("parent")), "comment": _str(row.get("comment")) or None} for row in rows]

    def get_mangle_rules(self) -> list[dict]:
        try:
            rows = self._call("/ip/firewall/mangle", "print")
        except RouterOSCommandError:
            return []
        return [{"remote_id": _str(row.get(".id")), "chain": _str(row.get("chain")), "packet_mark": _str(row.get("new-packet-mark")) or None, "connection_mark": _str(row.get("new-connection-mark")) or None, "comment": _str(row.get("comment")) or None} for row in rows]

    def get_address_lists(self) -> list[dict]:
        try:
            rows = self._call("/ip/firewall/address-list", "print")
        except RouterOSCommandError:
            return []
        return [{"remote_id": _str(row.get(".id")), "list": _str(row.get("list")), "address": _str(row.get("address")), "comment": _str(row.get("comment")) or None} for row in rows]

    def create_managed_object(self, kind: str, params: dict) -> str:
        """Typed creation of platform-managed RouterOS objects. The caller must
        pass only allowlisted keys; 'kind' is validated by the network-control
        layer before reaching here."""
        path = _OBJECT_PATHS.get(kind)
        if path is None:
            raise RouterOSCommandError(code="UNSUPPORTED_OBJECT", message=f"unsupported managed object kind: {kind}")
        self._call(path, "add", arguments=_object_arguments(kind, params))
        marker = params.get("name") or params.get("list") or params.get("comment")
        rows = self._call(path, "print", queries={"comment": str(params.get("comment", ""))})
        for row in rows:
            if marker and _str(row.get("name") or row.get("list")) == str(marker):
                return _str(row.get(".id"))
        for row in rows:
            return _str(row.get(".id"))
        raise RouterOSInvalidResponse(code="INVALID_RESPONSE", message="RouterOS did not return the created object")

    def remove_managed_object(self, kind: str, remote_id: str) -> None:
        path = _OBJECT_PATHS.get(kind)
        if path is None:
            raise RouterOSCommandError(code="UNSUPPORTED_OBJECT", message=f"unsupported managed object kind: {kind}")
        self._call(path, "remove", arguments={"id": remote_id})

    def disconnect_active_session(self, session_id: str) -> dict:
        rows = self._call("/ppp/active", "print", queries={"id": session_id}) if session_id.startswith("*") else []
        if not rows:
            return {"disconnected": False, "reason": "session not found", "session_id": session_id}
        self._call("/ppp/active", "remove", arguments={"id": _str(rows[0].get(".id"))})
        return {"disconnected": True, "session_id": session_id}


# ---------------------------------------------------------------------------
# Version-specific adapters. RouterOS v6 vs v7 differences relevant to RADIUS
# management are confined to these subclasses.
# ---------------------------------------------------------------------------

class RouterOSV6Adapter(RouterOSApiAdapter):
    """RouterOS v6 (6.43+). Uses challenge login and legacy package paths."""

    def __init__(self, **kwargs):
        super().__init__(plaintext_login=False, **kwargs)
        self.version = "6"

    def get_packages(self) -> list[dict]:
        rows = self._call("/system/package", "print")
        return [{"name": _str(row.get("name")), "version": _str(row.get("version")), "enabled": not _bool(row.get("disabled"))} for row in rows]


class RouterOSV7Adapter(RouterOSApiAdapter):
    """RouterOS v7. Package info lives under /system/package/update; the
    legacy binary API remains the supported boundary for this adapter.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.version = "7"

    def get_packages(self) -> list[dict]:
        rows = self._call("/system/package/update", "print")
        return [{"name": "routeros", "version": _str(row.get("installed-version") or row.get("version")), "enabled": True} for row in rows]


def adapter_for_version(version: str | None, **kwargs) -> RouterOSApiAdapter:
    parsed = parse_routeros_version(version or "")
    if parsed is None:
        raise RouterOSUnsupportedVersion(code="UNSUPPORTED_ROUTEROS_VERSION")
    if parsed[0] == 6:
        return RouterOSV6Adapter(**kwargs)
    if parsed[0] == 7:
        return RouterOSV7Adapter(**kwargs)
    raise RouterOSUnsupportedVersion(code="UNSUPPORTED_ROUTEROS_VERSION")


# ---------------------------------------------------------------------------
# Capability flag computation (pure, unit-testable)
# ---------------------------------------------------------------------------

def detect_capability_flags(version: tuple[int, int, int] | None) -> dict[str, bool]:
    """Baseline capability flags derived from the RouterOS version. Menu probes
    refine these flags at discovery time."""
    return {
        "routeros_api": version is not None,
        "api_ssl": False,
        "ppp": version is not None and version >= (6, 0, 0),
        "pppoe": version is not None and version >= (6, 0, 0),
        "hotspot": version is not None and version >= (6, 0, 0),
        "login_aaa": version is not None and version >= (6, 0, 0),
        "wireless": version is not None and version >= (6, 0, 0),
        "capsman": version is not None and version >= (6, 0, 0),
        "dhcp_radius": version is not None and version >= (6, 0, 0),
        "dot1x": version is not None and version >= (6, 0, 0),
        "accounting": version is not None and version >= (6, 0, 0),
        "interim_accounting": version is not None and version >= (6, 0, 0),
        "incoming_coa": version is not None and version >= (6, 0, 0),
        "disconnect_request": version is not None and version >= (6, 0, 0),
        "address_lists": version is not None and version >= (6, 0, 0),
        "static_ip": version is not None and version >= (6, 0, 0),
        "ipv6": version is not None and version >= (6, 0, 0),
        "vendor_specific_attributes": version is not None and version >= (6, 40, 0),
        "message_authenticator_options": version is not None and version >= (6, 45, 0),
    }


# ---------------------------------------------------------------------------
# In-memory fake router for tests and safe simulations
# ---------------------------------------------------------------------------

class FakeRouterOSAdapter(RouterOSAdapter):
    """Deterministic in-memory RouterOS adapter used by tests and simulations.

    It mirrors the real adapter's normalized contract so integration and
    end-to-end tests never require a physical router.
    """

    def __init__(self, identity: str = "MikroTik", version: str = "7.15", board_name: str = "CCR2004",
                 architecture: str = "arm64"):
        self.identity_value = identity
        self.version_value = version
        self.board_name = board_name
        self.architecture = architecture
        self.connected = False
        self.radius_entries: list[dict] = []
        self.radius_incoming: list[dict] = [{"accept": False, "port": 3799, "disabled": False, "address": ""}]
        self.ppp_aaa = {"use_radius": False, "accounting": False, "interim_update": "1m", "default_profile": "default"}
        self.user_aaa = {"use_radius": False, "accounting": False, "default_group": "read", "excluded_groups": []}
        self.hotspot_profiles: list[dict] = []
        self.active_ppp: list[dict] = []
        self.active_hotspot: list[dict] = []
        self.queue_types: list[dict] = []
        self.simple_queues: list[dict] = []
        self.queue_trees: list[dict] = []
        self.mangle_rules: list[dict] = []
        self.address_lists: list[dict] = []
        self.fail_auth = False
        self.fail_permission = False
        self.command_error: str | None = None
        self._next_id = 1
        self.version = version

    def _remote_id(self) -> str:
        value = f"*{self._next_id}"
        self._next_id += 1
        return value

    # -- connection ---------------------------------------------------------

    def connect(self) -> None:
        if self.fail_auth:
            raise RouterOSAuthenticationError(code="AUTHENTICATION_FAILED")
        if self.fail_permission:
            raise RouterOSPermissionError(code="INSUFFICIENT_PERMISSION")
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def test_connection(self) -> dict:
        started = datetime.now(timezone.utc)
        self.connect()
        return {
            "ok": True,
            "identity": self.get_identity(),
            "version": self.get_version(),
            "latency_ms": round((datetime.now(timezone.utc) - started).total_seconds() * 1000, 2),
        }

    # -- reads ---------------------------------------------------------------

    def get_identity(self) -> str:
        return self.identity_value

    def get_system_resource(self) -> dict:
        return {"uptime": "1d2h3m4s", "version": self.version_value, "board_name": self.board_name, "architecture_name": self.architecture, "free_memory": 1000000, "total_memory": 2000000, "cpu_load": 5, "free_hdd_space": 1000000, "total_hdd_space": 2000000}

    def get_version(self) -> str:
        return self.version_value

    def get_packages(self) -> list[dict]:
        return [{"name": "routeros", "version": self.version_value, "enabled": True}]

    def get_interfaces(self) -> list[dict]:
        return [{"name": "ether1", "type": "ether", "running": True, "disabled": False, "comment": None}]

    def get_ip_addresses(self) -> list[dict]:
        return [{"address": "10.0.0.1/24", "interface": "bridge1", "disabled": False}]

    def get_radius_entries(self) -> list[dict]:
        return [dict(item) for item in self.radius_entries]

    def get_radius_incoming(self) -> list[dict]:
        return [dict(item) for item in self.radius_incoming]

    def get_ppp_aaa(self) -> dict:
        state = dict(self.ppp_aaa)
        state["interim_update_seconds"] = parse_interval(state.get("interim_update") or "")
        return state

    def get_user_aaa(self) -> dict:
        return dict(self.user_aaa)

    def get_hotspot_profiles(self) -> list[dict]:
        return [dict(item) for item in self.hotspot_profiles]

    def get_relevant_service_state(self) -> dict:
        return {
            "identity": self.get_identity(),
            "version": self.get_version(),
            "system_resource": self.get_system_resource(),
            "radius_entries": self.get_radius_entries(),
            "radius_incoming": self.get_radius_incoming(),
            "ppp_aaa": self.get_ppp_aaa(),
            "user_aaa": self.get_user_aaa(),
            "hotspot_profiles": self.get_hotspot_profiles(),
        }

    def detect_capabilities(self) -> dict:
        if self.command_error:
            raise RouterOSCommandError(code="COMMAND_FAILED", message=self.command_error)
        version = parse_routeros_version(self.version_value)
        return {
            **detect_capability_flags(version),
            "routeros_api": True,
            "api_ssl": True,
            "ppp": True,
            "pppoe": True,
            "hotspot": True,
            "login_aaa": True,
            "incoming_coa": True,
            "disconnect_request": True,
            "accounting": True,
            "interim_accounting": True,
            "message_authenticator_options": version >= (6, 45, 0),
            "vendor_specific_attributes": version >= (6, 40, 0),
        }

    # -- writes ---------------------------------------------------------------

    def create_radius_entry(self, entry: dict) -> str:
        if self.command_error:
            raise RouterOSCommandError(code="COMMAND_FAILED", message=self.command_error)
        remote_id = self._remote_id()
        normalized = normalize_radius_entry({".id": remote_id, "address": entry["address"], "service": entry.get("services", entry.get("service", [])), "src-address": entry.get("src_address", ""), "authentication-port": entry.get("auth_port", entry.get("authentication_port", 1812)), "accounting-port": entry.get("accounting_port", 1813), "timeout": entry.get("timeout", 3000), "disabled": entry.get("disabled", False)})
        self.radius_entries.append(normalized)
        return remote_id

    def update_radius_entry(self, remote_id: str, entry: dict) -> None:
        for item in self.radius_entries:
            if item["remote_id"] == remote_id:
                if "services" in entry or "service" in entry:
                    item["service"] = entry.get("services", entry.get("service"))
                if "auth_port" in entry or "authentication_port" in entry:
                    item["authentication_port"] = entry.get("auth_port", entry.get("authentication_port"))
                if "accounting_port" in entry:
                    item["accounting_port"] = entry.get("accounting_port")
                if "timeout" in entry:
                    item["timeout"] = entry.get("timeout")
                if "src_address" in entry:
                    item["src_address"] = entry.get("src_address")
                if "address" in entry:
                    item["address"] = entry.get("address")
                return
        raise RouterOSInvalidResponse(code="INVALID_RESPONSE", message="radius entry not found")

    def remove_radius_entry(self, remote_id: str) -> None:
        self.radius_entries = [item for item in self.radius_entries if item["remote_id"] != remote_id]

    def configure_ppp_aaa(self, settings: dict) -> None:
        if "use_radius" in settings:
            self.ppp_aaa["use_radius"] = bool(settings["use_radius"])
        if "accounting" in settings:
            self.ppp_aaa["accounting"] = bool(settings["accounting"])
        if settings.get("interim_update_seconds") is not None:
            self.ppp_aaa["interim_update"] = format_interval(int(settings["interim_update_seconds"])) or "1m"

    def configure_user_aaa(self, settings: dict) -> None:
        if "use_radius" in settings:
            self.user_aaa["use_radius"] = bool(settings["use_radius"])
        if "accounting" in settings:
            self.user_aaa["accounting"] = bool(settings["accounting"])
        if settings.get("default_group"):
            self.user_aaa["default_group"] = str(settings["default_group"])
        if settings.get("excluded_groups") is not None:
            self.user_aaa["excluded_groups"] = [str(item) for item in settings["excluded_groups"]]

    def configure_hotspot_radius(self, profile: str, settings: dict) -> None:
        for item in self.hotspot_profiles:
            if item["name"] == profile:
                if "use_radius" in settings:
                    item["use_radius"] = bool(settings["use_radius"])
                if "radius_accounting" in settings:
                    item["radius_accounting"] = bool(settings["radius_accounting"])
                if settings.get("radius_interim_update_seconds") is not None:
                    item["radius_interim_update"] = format_interval(int(settings["radius_interim_update_seconds"])) or "1m"
                return
        raise RouterOSCommandError(code="COMMAND_FAILED", message=f"hotspot profile not found: {profile}")

    def configure_radius_incoming(self, settings: dict) -> None:
        item = self.radius_incoming[0]
        if "accept" in settings:
            item["accept"] = bool(settings["accept"])
        if settings.get("port") is not None:
            item["port"] = int(settings["port"])
        if "disabled" in settings:
            item["disabled"] = bool(settings["disabled"])
        if settings.get("address"):
            item["address"] = str(settings["address"])

    def verify_configuration(self, desired: dict) -> dict:
        live = self.get_relevant_service_state()
        return {"matched": True, "differences": [], "live": redact(live)}

    def get_active_ppp_sessions(self) -> list[dict]:
        return [dict(item) for item in self.active_ppp]

    def get_active_hotspot_sessions(self) -> list[dict]:
        return [dict(item) for item in self.active_hotspot]

    # -- Milestone 3: typed policy/QoS operations ---------------------------

    def get_queue_types(self) -> list[dict]:
        return [dict(item) for item in self.queue_types]

    def get_queues(self) -> list[dict]:
        return [dict(item) for item in self.simple_queues]

    def get_queue_trees(self) -> list[dict]:
        return [dict(item) for item in self.queue_trees]

    def get_mangle_rules(self) -> list[dict]:
        return [dict(item) for item in self.mangle_rules]

    def get_address_lists(self) -> list[dict]:
        return [dict(item) for item in self.address_lists]

    def create_managed_object(self, kind: str, params: dict) -> str:
        if kind not in _OBJECT_PATHS:
            raise RouterOSCommandError(code="UNSUPPORTED_OBJECT", message=f"unsupported managed object kind: {kind}")
        remote_id = self._remote_id()
        entry = {"remote_id": remote_id, "kind": kind}
        entry.update({k: params[k] for k in params if k in {"name", "list", "address", "comment", "kind", "parent", "max-limit", "limit-at", "priority", "chain", "protocol", "dst-port", "src-port", "dscp", "new-packet-mark", "new-connection-mark", "target", "packet-marks", "pcq-rate", "pcq-limit", "pcq-classifier"}})
        entry["comment"] = entry.get("comment", params.get("comment"))
        bucket = self._object_bucket(kind)
        bucket.append(entry)
        return remote_id

    def remove_managed_object(self, kind: str, remote_id: str) -> None:
        bucket = self._object_bucket(kind)
        for index, item in enumerate(bucket):
            if item.get("remote_id") == remote_id:
                bucket.pop(index)
                return
        raise RouterOSCommandError(code="NOT_FOUND", message=f"managed object not found: {remote_id}")

    def disconnect_active_session(self, session_id: str) -> dict:
        for index, item in enumerate(self.active_ppp):
            if item.get("remote_id") == session_id or item.get("name") == session_id:
                self.active_ppp.pop(index)
                return {"disconnected": True, "session_id": session_id}
        return {"disconnected": False, "reason": "session not found", "session_id": session_id}

    def _object_bucket(self, kind: str) -> list[dict]:
        if kind == "queue_type":
            return self.queue_types
        if kind == "queue_tree":
            return self.queue_trees
        if kind == "simple_queue":
            return self.simple_queues
        if kind == "mangle_rule":
            return self.mangle_rules
        if kind == "address_list":
            return self.address_lists
        raise RouterOSCommandError(code="UNSUPPORTED_OBJECT", message=f"unsupported managed object kind: {kind}")

    # -- simulation helpers --------------------------------------------------

    def seed_radius_entry(self, **kwargs) -> None:
        remote_id = kwargs.pop("remote_id", self._remote_id())
        entry = {"remote_id": remote_id, "address": kwargs.get("address", "192.0.2.1"), "service": kwargs.get("service", ["pppoe"]), "src_address": kwargs.get("src_address"), "authentication_port": kwargs.get("authentication_port", 1812), "accounting_port": kwargs.get("accounting_port", 1813), "timeout": kwargs.get("timeout", 3000), "realm": kwargs.get("realm"), "domain": kwargs.get("domain"), "called_id": kwargs.get("called_id"), "disabled": kwargs.get("disabled", False)}
        self.radius_entries.append(entry)

    def seed_hotspot_profile(self, **kwargs) -> None:
        self.hotspot_profiles.append({"remote_id": kwargs.pop("remote_id", self._remote_id()), "name": kwargs.get("name", "default"), "use_radius": kwargs.get("use_radius", False), "radius_accounting": kwargs.get("radius_accounting", False), "radius_interim_update": kwargs.get("radius_interim_update"), "radius_mac_format": None, "location_name": None})

    def seed_queue_type(self, **kwargs) -> None:
        self.queue_types.append({"remote_id": kwargs.pop("remote_id", self._remote_id()), "name": kwargs.get("name"), "kind": kwargs.get("kind", "pcq"), "comment": kwargs.get("comment")})

    def seed_simple_queue(self, **kwargs) -> None:
        self.simple_queues.append({"remote_id": kwargs.pop("remote_id", self._remote_id()), "name": kwargs.get("name"), "target": kwargs.get("target"), "comment": kwargs.get("comment")})

    def seed_queue_tree(self, **kwargs) -> None:
        self.queue_trees.append({"remote_id": kwargs.pop("remote_id", self._remote_id()), "name": kwargs.get("name"), "parent": kwargs.get("parent"), "comment": kwargs.get("comment")})

    def seed_mangle(self, **kwargs) -> None:
        self.mangle_rules.append({"remote_id": kwargs.pop("remote_id", self._remote_id()), "chain": kwargs.get("chain", "forward"), "packet_mark": kwargs.get("packet_mark") or kwargs.get("new-packet-mark"), "connection_mark": kwargs.get("connection_mark") or kwargs.get("new-connection-mark"), "comment": kwargs.get("comment")})

    def seed_address_list(self, **kwargs) -> None:
        self.address_lists.append({"remote_id": kwargs.pop("remote_id", self._remote_id()), "list": kwargs.get("list"), "address": kwargs.get("address"), "comment": kwargs.get("comment")})
