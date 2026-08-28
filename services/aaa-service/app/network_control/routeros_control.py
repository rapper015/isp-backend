"""Typed RouterOS control surface and non-destructive readiness check.

The domain/policy layers never call RouterOS commands directly; they go through
this facade, which only exposes allowlisted typed operations. Arbitrary console
commands, reboot, factory reset and other prohibited operations are rejected.
"""
from __future__ import annotations

from typing import Any, Callable

from ..routeros import (
    FakeRouterOSAdapter,
    RouterOSAdapter,
    RouterOSAuthenticationError,
    RouterOSConnectionError,
    RouterOSTlsError,
    RouterOSUnsupportedDevice,
    RouterOSUnsupportedVersion,
)
from .enums import PROHIBITED_OPERATIONS, ROUTEROS_OPERATIONS

READINESS_STATUSES = (
    "READY",
    "READY_WITH_WARNINGS",
    "MISSING_CONFIGURATION",
    "UNSUPPORTED",
    "UNREACHABLE",
    "AUTHENTICATION_FAILED",
    "TLS_VALIDATION_FAILED",
)


class ProhibitedOperationError(ValueError):
    pass


class RouterOSControl:
    """Thin facade over a RouterOSAdapter exposing only allowlisted operations."""

    # Operation (allowlist) -> (adapter method, positional prefix).
    _OPERATION_MAP = {
        "test_connection": ("test_connection", ()),
        "discover_router": ("detect_capabilities", ()),
        "read_radius_configuration": ("get_radius_entries", ()),
        "read_radius_incoming": ("get_radius_incoming", ()),
        "read_ppp_aaa": ("get_ppp_aaa", ()),
        "read_hotspot_profiles": ("get_hotspot_profiles", ()),
        "read_active_ppp_sessions": ("get_active_ppp_sessions", ()),
        "read_active_hotspot_sessions": ("get_active_hotspot_sessions", ()),
        "read_ip_pools": ("get_ip_addresses", ()),
        "read_queues": ("get_queues", ()),
        "read_queue_types": ("get_queue_types", ()),
        "read_queue_trees": ("get_queue_trees", ()),
        "read_mangle_rules": ("get_mangle_rules", ()),
        "read_address_lists": ("get_address_lists", ()),
        "create_managed_queue_type": ("create_managed_object", ("queue_type",)),
        "create_managed_queue_tree": ("create_managed_object", ("queue_tree",)),
        "create_managed_pcq": ("create_managed_object", ("queue_type",)),
        "create_managed_mangle": ("create_managed_object", ("mangle_rule",)),
        "create_managed_address_list": ("create_managed_object", ("address_list",)),
        "remove_managed_object": ("remove_managed_object", ()),
        "disconnect_session": ("disconnect_active_session", ()),
        "verify_applied_policy": ("verify_configuration", ()),
    }

    def __init__(self, adapter: RouterOSAdapter):
        self.adapter = adapter

    def call(self, operation: str, *args, **kwargs) -> Any:
        if operation in PROHIBITED_OPERATIONS:
            raise ProhibitedOperationError(f"prohibited RouterOS operation: {operation}")
        if operation not in ROUTEROS_OPERATIONS:
            raise ProhibitedOperationError(f"operation not allowlisted: {operation}")
        mapping = self._OPERATION_MAP.get(operation)
        if mapping is None:
            raise ProhibitedOperationError(f"operation not supported by facade: {operation}")
        method_name, prefix = mapping
        method = getattr(self.adapter, method_name, None)
        if method is None:
            raise ProhibitedOperationError(f"adapter does not support operation: {operation}")
        return method(*prefix, *args, **kwargs)


def run_readiness_check(adapter: RouterOSAdapter, nas=None, tenant_id=None) -> dict:
    """Non-destructive readiness check. Never changes router-wide config.

    Returns {"status", "checks", "winbox_guide"} where winbox_guide lists only
    the missing configuration (no secrets)."""
    checks: dict[str, Any] = {}
    try:
        connection = adapter.test_connection()
        checks["reachable"] = {"ok": True, "identity": connection.get("identity"), "version": connection.get("version"), "latency_ms": connection.get("latency_ms")}
    except RouterOSAuthenticationError:
        return {"status": "AUTHENTICATION_FAILED", "checks": {"reachable": {"ok": False, "reason": "authentication failed"}}, "winbox_guide": {"summary": "API credentials are invalid or insufficient", "steps": []}}
    except RouterOSTlsError:
        return {"status": "TLS_VALIDATION_FAILED", "checks": {"reachable": {"ok": False, "reason": "TLS certificate validation failed"}}, "winbox_guide": {"summary": "TLS certificate validation failed", "steps": ["Install/trust the router certificate on the management host", "Verify the management address and port"]}}
    except RouterOSConnectionError as error:
        return {"status": "UNREACHABLE", "checks": {"reachable": {"ok": False, "reason": str(error)}}, "winbox_guide": {"summary": "Router is unreachable", "steps": ["Check network reachability and firewall rules", "Confirm the API/API-SSL service is enabled in Winbox"]}}

    missing: list[str] = []
    warnings: list[str] = []
    unsupported: list[str] = []

    try:
        version = adapter.get_version()
        capabilities = adapter.detect_capabilities()
        checks["version"] = {"ok": True, "version": version}
        if not capabilities.get("routeros_api"):
            unsupported.append("RouterOS API service is not available")
    except (RouterOSUnsupportedVersion, RouterOSUnsupportedDevice) as error:
        return {"status": "UNSUPPORTED", "checks": {"version": {"ok": False, "reason": str(error)}}, "winbox_guide": {"summary": "RouterOS version/device unsupported", "steps": []}}

    # RADIUS client configuration.
    radius_entries = adapter.get_radius_entries()
    checks["radius_client"] = {"ok": bool(radius_entries), "count": len(radius_entries)}
    if not radius_entries:
        missing.append("RADIUS client entry pointing to the AAA service")

    # RADIUS incoming (CoA/Disconnect).
    incoming = adapter.get_radius_incoming()
    incoming_ok = bool(incoming) and any(item.get("accept") for item in incoming)
    checks["radius_incoming"] = {"ok": incoming_ok, "entries": len(incoming)}
    if not incoming_ok:
        missing.append("RADIUS incoming (CoA/Disconnect) with 'accept' enabled")

    # PPP AAA + accounting.
    ppp_aaa = adapter.get_ppp_aaa()
    ppp_ok = bool(ppp_aaa.get("use_radius")) and bool(ppp_aaa.get("accounting"))
    checks["ppp_aaa"] = {"ok": ppp_ok, "use_radius": bool(ppp_aaa.get("use_radius")), "accounting": bool(ppp_aaa.get("accounting")), "interim_update": ppp_aaa.get("interim_update")}
    if not bool(ppp_aaa.get("use_radius")):
        missing.append("PPP AAA 'use radius' enabled")
    if not bool(ppp_aaa.get("accounting")):
        missing.append("PPP AAA 'accounting' enabled")
    interim_seconds = ppp_aaa.get("interim_update_seconds")
    if not interim_seconds:
        warnings.append("PPP AAA interim update is not configured")

    # Hotspot RADIUS (informational for PPPoE-only deployments).
    hotspot = adapter.get_hotspot_profiles()
    checks["hotspot_radius"] = {"profiles": len(hotspot)}
    if hotspot:
        enabled = [p for p in hotspot if p.get("use_radius")]
        if not enabled:
            warnings.append("Hotspot profiles exist but RADIUS is not enabled")

    # Clock / NTP (read via system resource is not enough; warn only).
    checks["ntp"] = {"ok": None, "note": "verify NTP clients in Winbox"}

    # Queue capability.
    checks["queue_capability"] = {"ok": capabilities.get("queue") if capabilities.get("queue") is not None else None}
    if capabilities.get("queue") is False:
        unsupported.append("Queue support is unavailable")

    if unsupported:
        status = "UNSUPPORTED"
    elif missing:
        status = "MISSING_CONFIGURATION"
    elif warnings:
        status = "READY_WITH_WARNINGS"
    else:
        status = "READY"

    guide = build_winbox_guide(missing, warnings, nas)
    return {"status": status, "checks": checks, "winbox_guide": guide}


def build_winbox_guide(missing: list[str], warnings: list[str], nas=None) -> dict:
    """Tenant/device-specific setup checklist containing only missing config,
    with no shared secrets."""
    steps: list[str] = []
    summary = "No missing configuration." if not missing else "Complete the following steps in Winbox on the router."
    for item in missing:
        steps.append(_winbox_step(item))
    for item in warnings:
        steps.append(_winbox_step(item, warning=True))
    return {"summary": summary, "steps": steps, "device": nas.name if nas and getattr(nas, "name", None) else None}


def _winbox_step(item: str, warning: bool = False) -> str:
    prefix = "Recommended: " if warning else "Required: "
    return f"{prefix}{item} (see Operational Runbook for exact menus)"
