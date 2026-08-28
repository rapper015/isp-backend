"""Explicit NAS orchestration state machines; callers cannot set states freely.

Four validated state machines are defined:

* ``LIFECYCLE`` - the NAS onboarding lifecycle
* ``JOB_STATES`` - Router configuration job state machine
* ``REGISTRATION`` - manual FreeRADIUS registration tracking state machine
* ``SECRET_ROTATION`` - staged shared-secret rotation state machine
"""
from __future__ import annotations

LIFECYCLE = {
    "DRAFT": {"CONNECTION_PENDING", "DISABLED", "DECOMMISSIONING"},
    "CONNECTION_PENDING": {"CONNECTION_TESTING", "FAILED", "DISABLED", "DECOMMISSIONING"},
    "CONNECTION_TESTING": {"CONNECTED", "FAILED"},
    "CONNECTED": {"DISCOVERING", "DISABLED", "DECOMMISSIONING"},
    "DISCOVERING": {"DISCOVERED", "FAILED"},
    "DISCOVERED": {"RADIUS_REGISTRATION_PENDING", "CONFIGURATION_PENDING", "DISABLED", "DECOMMISSIONING"},
    "CONFIGURATION_PENDING": {"CONFIGURATION_PLANNED", "FAILED", "DISABLED"},
    "CONFIGURATION_PLANNED": {"AWAITING_APPROVAL", "CONFIGURING", "FAILED", "DISABLED"},
    "AWAITING_APPROVAL": {"CONFIGURING", "DISABLED"},
    "CONFIGURING": {"VERIFYING", "FAILED"},
    "VERIFYING": {"CONFIGURED", "DEGRADED", "FAILED"},
    "CONFIGURED": {"RADIUS_REGISTRATION_PENDING", "TESTING", "DEGRADED", "DISABLED"},
    "RADIUS_REGISTRATION_PENDING": {"RADIUS_REGISTRATION_CONFIRMED", "FAILED", "DISABLED"},
    "RADIUS_REGISTRATION_CONFIRMED": {"TESTING", "CONFIGURATION_PENDING", "DISABLED"},
    "TESTING": {"ACTIVE", "DEGRADED", "FAILED"},
    "ACTIVE": {"DEGRADED", "DISABLED", "DECOMMISSIONING"},
    "DEGRADED": {"CONFIGURATION_PENDING", "ACTIVE", "DISABLED"},
    "FAILED": {"CONNECTION_PENDING", "DISABLED", "DECOMMISSIONING"},
    "DISABLED": {"CONNECTION_PENDING", "DECOMMISSIONING"},
    "DECOMMISSIONING": {"DECOMMISSIONED"},
    "DECOMMISSIONED": set(),
}

JOB_STATES = {
    "PENDING": {"QUEUED", "CANCELLED", "FAILED"},
    "QUEUED": {"RUNNING", "CANCELLED", "FAILED"},
    "RUNNING": {"VERIFYING", "FAILED", "ROLLBACK_PENDING"},
    "VERIFYING": {"SUCCEEDED", "FAILED", "ROLLBACK_PENDING"},
    "SUCCEEDED": {"ROLLBACK_PENDING"},
    "FAILED": {"ROLLBACK_PENDING"},
    "ROLLBACK_PENDING": {"ROLLING_BACK", "ROLLBACK_FAILED"},
    "ROLLING_BACK": {"ROLLED_BACK", "ROLLBACK_FAILED"},
    "ROLLED_BACK": set(),
    "ROLLBACK_FAILED": set(),
    "CANCELLED": set(),
}

REGISTRATION = {
    "NOT_REQUIRED": {"PENDING", "DISABLED"},
    "PENDING": {"DETAILS_GENERATED", "DISABLED"},
    "DETAILS_GENERATED": {"AWAITING_MANUAL_CONFIGURATION", "DISABLED"},
    "AWAITING_MANUAL_CONFIGURATION": {"MANUALLY_CONFIRMED", "DISABLED"},
    "MANUALLY_CONFIRMED": {"VERIFICATION_PENDING", "DISABLED"},
    "VERIFICATION_PENDING": {"VERIFIED", "VERIFICATION_FAILED"},
    "VERIFICATION_FAILED": {"AWAITING_MANUAL_CONFIGURATION", "VERIFICATION_PENDING", "DISABLED"},
    "VERIFIED": {"SECRET_ROTATION_PENDING", "VERIFICATION_PENDING", "DISABLED"},
    "SECRET_ROTATION_PENDING": {"PENDING", "VERIFIED", "DISABLED"},
    "DISABLED": {"PENDING"},
}

SECRET_ROTATION = {
    "ROTATION_DRAFT": {"NEW_SECRET_GENERATED", "FAILED"},
    "NEW_SECRET_GENERATED": {"AWAITING_FREERADIUS_UPDATE", "ROTATION_DRAFT", "FAILED"},
    "AWAITING_FREERADIUS_UPDATE": {"FREERADIUS_UPDATE_CONFIRMED", "ROTATION_DRAFT", "FAILED"},
    "FREERADIUS_UPDATE_CONFIRMED": {"ROUTER_UPDATE_PENDING", "ROLLBACK_PENDING", "FAILED"},
    "ROUTER_UPDATE_PENDING": {"ROUTER_UPDATED", "ROLLBACK_PENDING", "FAILED"},
    "ROUTER_UPDATED": {"VERIFYING", "ROLLBACK_PENDING", "FAILED"},
    "VERIFYING": {"ACTIVE", "ROLLBACK_PENDING", "FAILED"},
    "ACTIVE": set(),
    "ROLLBACK_PENDING": {"ROLLED_BACK", "FAILED"},
    "ROLLED_BACK": set(),
    "FAILED": set(),
}


def _transition(state_map: dict[str, set[str]], current: str, target: str, label: str) -> str:
    if target not in state_map.get(current, set()):
        raise ValueError(f"invalid {label} transition: {current} -> {target}")
    return target


def transition(current: str, target: str) -> str:
    """Validate a NAS lifecycle transition. Raises ValueError when invalid."""
    return _transition(LIFECYCLE, current, target, "NAS lifecycle")


def job_transition(current: str, target: str) -> str:
    return _transition(JOB_STATES, current, target, "configuration job")


def registration_transition(current: str, target: str) -> str:
    return _transition(REGISTRATION, current, target, "FreeRADIUS registration")


def secret_rotation_transition(current: str, target: str) -> str:
    return _transition(SECRET_ROTATION, current, target, "secret rotation")


def is_terminal_lifecycle(state: str) -> bool:
    return not LIFECYCLE.get(state, set())
