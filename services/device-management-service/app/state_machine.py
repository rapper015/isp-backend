"""Validated state machines for device onboarding, configuration jobs, device
actions, diagnostics, firmware deployments and rollouts. No code mutates these
states directly; every change goes through the transition functions."""

# ---------------------------------------------------------------------------
# Managed CPE onboarding / lifecycle
# ---------------------------------------------------------------------------
DEVICE_TRANSITIONS = {
    "DISCOVERED": {"QUARANTINED", "IDENTIFIED", "REJECTED", "DUPLICATE", "SECURITY_HOLD"},
    "QUARANTINED": {"IDENTIFIED", "CLAIM_PENDING", "REJECTED", "DUPLICATE", "SECURITY_HOLD"},
    "IDENTIFIED": {"CLAIM_PENDING", "QUARANTINED", "REJECTED", "DUPLICATE", "SECURITY_HOLD"},
    "CLAIM_PENDING": {"CLAIMED", "QUARANTINED", "REJECTED", "SECURITY_HOLD"},
    "CLAIMED": {"INVENTORY_MATCHED", "ASSIGNED", "QUARANTINED", "REJECTED", "DUPLICATE", "DECOMMISSIONED"},
    "INVENTORY_MATCHED": {"ASSIGNED", "PROVISIONING", "ACTIVE", "QUARANTINED", "DECOMMISSIONED"},
    "ASSIGNED": {"PROVISIONING", "ACTIVE", "QUARANTINED", "DECOMMISSIONED"},
    "PROVISIONING": {"ACTIVE", "QUARANTINED", "SECURITY_HOLD", "DECOMMISSIONED"},
    "ACTIVE": {"OFFLINE", "PROVISIONING", "QUARANTINED", "SECURITY_HOLD", "DECOMMISSIONED", "DUPLICATE"},
    "OFFLINE": {"ACTIVE", "QUARANTINED", "DECOMMISSIONED"},
    "REJECTED": set(),
    "DUPLICATE": {"QUARANTINED", "DECOMMISSIONED"},
    "SECURITY_HOLD": {"QUARANTINED", "ACTIVE", "DECOMMISSIONED"},
    "DECOMMISSIONED": set(),
}

# ---------------------------------------------------------------------------
# Configuration jobs
# ---------------------------------------------------------------------------
CONFIGURATION_JOB_TRANSITIONS = {
    "DRAFT": {"VALIDATING", "CANCELLED"},
    "VALIDATING": {"READY", "FAILED", "CANCELLED"},
    "READY": {"QUEUED", "CANCELLED"},
    "QUEUED": {"CONNECTION_REQUEST_PENDING", "WAITING_FOR_INFORM", "CANCELLED", "FAILED", "TIMED_OUT"},
    "CONNECTION_REQUEST_PENDING": {"WAITING_FOR_INFORM", "EXECUTING", "TIMED_OUT", "FAILED", "CANCELLED"},
    "WAITING_FOR_INFORM": {"EXECUTING", "TIMED_OUT", "FAILED", "CANCELLED"},
    "EXECUTING": {"DEVICE_ACKNOWLEDGED", "VERIFYING", "FAILED", "TIMED_OUT", "ROLLBACK_PENDING"},
    "DEVICE_ACKNOWLEDGED": {"VERIFYING", "FAILED", "TIMED_OUT"},
    "VERIFYING": {"SUCCEEDED", "FAILED", "ROLLBACK_PENDING", "MANUAL_INTERVENTION_REQUIRED"},
    "SUCCEEDED": set(),
    "FAILED": {"ROLLBACK_PENDING", "MANUAL_INTERVENTION_REQUIRED"},
    "TIMED_OUT": {"ROLLBACK_PENDING", "MANUAL_INTERVENTION_REQUIRED"},
    "CANCELLED": set(),
    "ROLLBACK_PENDING": {"ROLLED_BACK", "FAILED", "MANUAL_INTERVENTION_REQUIRED"},
    "ROLLED_BACK": set(),
    "MANUAL_INTERVENTION_REQUIRED": {"ROLLBACK_PENDING", "FAILED", "CANCELLED"},
}

# ---------------------------------------------------------------------------
# Device actions
# ---------------------------------------------------------------------------
DEVICE_ACTION_TRANSITIONS = {
    "REQUESTED": {"AUTHORIZATION_REQUIRED", "APPROVED", "QUEUED", "CANCELLED", "FAILED"},
    "AUTHORIZATION_REQUIRED": {"APPROVED", "CANCELLED"},
    "APPROVED": {"QUEUED", "CANCELLED"},
    "QUEUED": {"WAITING_FOR_DEVICE", "EXECUTING", "CANCELLED", "FAILED", "TIMED_OUT"},
    "WAITING_FOR_DEVICE": {"EXECUTING", "TIMED_OUT", "CANCELLED", "FAILED"},
    "EXECUTING": {"VERIFYING", "SUCCEEDED", "FAILED", "TIMED_OUT", "MANUAL_INTERVENTION_REQUIRED"},
    "VERIFYING": {"SUCCEEDED", "FAILED", "MANUAL_INTERVENTION_REQUIRED"},
    "SUCCEEDED": set(),
    "FAILED": set(),
    "TIMED_OUT": set(),
    "CANCELLED": set(),
    "MANUAL_INTERVENTION_REQUIRED": {"QUEUED", "CANCELLED", "FAILED"},
}

# ---------------------------------------------------------------------------
# Diagnostic jobs
# ---------------------------------------------------------------------------
DIAGNOSTIC_JOB_TRANSITIONS = {
    "REQUESTED": {"VALIDATING", "CANCELLED", "UNSUPPORTED"},
    "VALIDATING": {"QUEUED", "UNSUPPORTED", "FAILED", "CANCELLED"},
    "QUEUED": {"WAITING_FOR_DEVICE", "RUNNING", "CANCELLED", "FAILED", "TIMED_OUT"},
    "WAITING_FOR_DEVICE": {"RUNNING", "TIMED_OUT", "CANCELLED", "FAILED"},
    "RUNNING": {"COLLECTING_RESULTS", "FAILED", "TIMED_OUT", "CANCELLED"},
    "COLLECTING_RESULTS": {"SUCCEEDED", "FAILED", "TIMED_OUT"},
    "SUCCEEDED": set(),
    "FAILED": set(),
    "TIMED_OUT": set(),
    "UNSUPPORTED": set(),
    "CANCELLED": set(),
}

# ---------------------------------------------------------------------------
# Firmware deployments
# ---------------------------------------------------------------------------
FIRMWARE_DEPLOYMENT_TRANSITIONS = {
    "QUEUED": {"CONNECTION_REQUEST_PENDING", "TRANSFERRING", "FAILED"},
    "CONNECTION_REQUEST_PENDING": {"TRANSFERRING", "WAITING_FOR_INFORM", "FAILED", "TIMED_OUT"},
    "TRANSFERRING": {"TRANSFERRED", "FAILED", "TIMED_OUT"},
    "TRANSFERRED": {"REBOOTING", "VERIFYING", "FAILED"},
    "REBOOTING": {"WAITING_FOR_INFORM", "FAILED", "TIMED_OUT"},
    "WAITING_FOR_INFORM": {"VERIFYING", "FAILED", "TIMED_OUT"},
    "VERIFYING": {"SUCCEEDED", "FAILED", "ROLLED_BACK", "QUARANTINED", "MANUAL_INTERVENTION_REQUIRED"},
    "SUCCEEDED": set(),
    "FAILED": {"ROLLED_BACK", "QUARANTINED", "MANUAL_INTERVENTION_REQUIRED"},
    "ROLLED_BACK": set(),
    "QUARANTINED": set(),
    "MANUAL_INTERVENTION_REQUIRED": {"ROLLED_BACK", "QUARANTINED", "FAILED"},
}

# ---------------------------------------------------------------------------
# Firmware rollouts
# ---------------------------------------------------------------------------
ROLLOUT_TRANSITIONS = {
    "DRAFT": {"READY", "STOPPED"},
    "READY": {"RUNNING", "STOPPED"},
    "RUNNING": {"PAUSED", "AUTO_PAUSED", "COMPLETED", "STOPPED", "FAILED"},
    "PAUSED": {"RUNNING", "STOPPED"},
    "AUTO_PAUSED": {"RUNNING", "STOPPED"},
    "COMPLETED": set(),
    "STOPPED": set(),
    "FAILED": set(),
}

ROLLOUT_STAGE_TRANSITIONS = {
    "PENDING": {"RUNNING", "SKIPPED", "STOPPED"},
    "RUNNING": {"SUCCEEDED", "FAILED", "PAUSED", "STOPPED"},
    "SUCCEEDED": set(),
    "FAILED": {"PENDING", "STOPPED"},
    "PAUSED": {"RUNNING", "STOPPED"},
    "SKIPPED": set(),
    "STOPPED": set(),
}


def _transition(map_: dict, current: str, target: str) -> str:
    if current not in map_:
        raise ValueError(f"unknown state {current!r}")
    if target not in map_[current]:
        raise ValueError(f"invalid transition: {current} -> {target}")
    return target


def device_transition(current: str, target: str) -> str:
    return _transition(DEVICE_TRANSITIONS, current, target)


def configuration_job_transition(current: str, target: str) -> str:
    return _transition(CONFIGURATION_JOB_TRANSITIONS, current, target)


def device_action_transition(current: str, target: str) -> str:
    return _transition(DEVICE_ACTION_TRANSITIONS, current, target)


def diagnostic_job_transition(current: str, target: str) -> str:
    return _transition(DIAGNOSTIC_JOB_TRANSITIONS, current, target)


def firmware_deployment_transition(current: str, target: str) -> str:
    return _transition(FIRMWARE_DEPLOYMENT_TRANSITIONS, current, target)


def rollout_transition(current: str, target: str) -> str:
    return _transition(ROLLOUT_TRANSITIONS, current, target)


def rollout_stage_transition(current: str, target: str) -> str:
    return _transition(ROLLOUT_STAGE_TRANSITIONS, current, target)
