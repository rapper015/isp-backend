"""Validated state machines for tenant lifecycle, provisioning saga, partner
lifecycle, settlement, dispute and transfer workflows. Every state change goes
through a transition function; invalid transitions raise ValueError."""
from .domain.exceptions import StateTransitionError

# ---------------------------------------------------------------------------
# Tenant lifecycle
# ---------------------------------------------------------------------------
TENANT_TRANSITIONS = {
    "REQUESTED": {"VALIDATING", "PROVISIONING", "FAILED", "ARCHIVED"},
    "VALIDATING": {"PROVISIONING", "FAILED", "REQUESTED"},
    "PROVISIONING": {"ACTIVE", "FAILED", "ROLLING_BACK", "RESTRICTED"},
    "ACTIVE": {"RESTRICTED", "SUSPENDED", "OFFBOARDING"},
    "RESTRICTED": {"ACTIVE", "SUSPENDED", "OFFBOARDING"},
    "SUSPENDED": {"ACTIVE", "RESTRICTED", "OFFBOARDING"},
    "OFFBOARDING": {"ARCHIVED", "ACTIVE"},
    "ARCHIVED": set(),
    "FAILED": {"PROVISIONING", "ARCHIVED"},
}

# ---------------------------------------------------------------------------
# Provisioning saga
# ---------------------------------------------------------------------------
PROVISIONING_TRANSITIONS = {
    "REQUESTED": {"VALIDATING", "FAILED"},
    "VALIDATING": {"PROVISIONING_CONTROL_RECORD", "FAILED"},
    "PROVISIONING_CONTROL_RECORD": {"PROVISIONING_DATABASE", "FAILED"},
    "PROVISIONING_DATABASE": {"RUNNING_MIGRATIONS", "FAILED"},
    "RUNNING_MIGRATIONS": {"CREATING_STORAGE_NAMESPACE", "FAILED"},
    "CREATING_STORAGE_NAMESPACE": {"CREATING_MESSAGING_NAMESPACE", "FAILED"},
    "CREATING_MESSAGING_NAMESPACE": {"CONFIGURING_DEFAULTS", "FAILED"},
    "CONFIGURING_DEFAULTS": {"CREATING_ADMIN", "FAILED"},
    "CREATING_ADMIN": {"VERIFYING", "FAILED"},
    "VERIFYING": {"ACTIVE", "FAILED", "ROLLING_BACK"},
    "ACTIVE": set(),
    "FAILED": {"ROLLING_BACK", "MANUAL_INTERVENTION_REQUIRED"},
    "ROLLING_BACK": {"MANUAL_INTERVENTION_REQUIRED", "REQUESTED"},
    "MANUAL_INTERVENTION_REQUIRED": {"ROLLING_BACK", "REQUESTED"},
}

# ---------------------------------------------------------------------------
# Partner lifecycle
# ---------------------------------------------------------------------------
PARTNER_TRANSITIONS = {
    "PROSPECT": {"ONBOARDING", "SUSPENDED", "TERMINATED", "ARCHIVED"},
    "ONBOARDING": {"ACTIVE", "SUSPENDED", "FAILED"},
    "ACTIVE": {"RESTRICTED", "SUSPENDED", "TERMINATING", "ARCHIVED"},
    "RESTRICTED": {"ACTIVE", "SUSPENDED", "TERMINATING"},
    "SUSPENDED": {"ACTIVE", "RESTRICTED", "TERMINATING"},
    "TERMINATING": {"TERMINATED", "ACTIVE"},
    "TERMINATED": {"ARCHIVED"},
    "ARCHIVED": set(),
    "FAILED": {"ONBOARDING", "ARCHIVED"},
}

# ---------------------------------------------------------------------------
# Settlement lifecycle
# ---------------------------------------------------------------------------
SETTLEMENT_TRANSITIONS = {
    "DRAFT": {"CALCULATING", "CANCELLED"},
    "CALCULATING": {"CALCULATED", "FAILED"},
    "CALCULATED": {"UNDER_REVIEW", "DISPUTED", "CANCELLED"},
    "UNDER_REVIEW": {"APPROVED", "DISPUTED", "CANCELLED"},
    "APPROVED": {"LOCKED", "DISPUTED", "CANCELLED"},
    "LOCKED": {"PAYOUT_PENDING", "DISPUTED", "REVERSED"},
    "PAYOUT_PENDING": {"PARTIALLY_PAID", "PAID", "REVERSED"},
    "PARTIALLY_PAID": {"PAID", "RECONCILING", "REVERSED"},
    "PAID": {"RECONCILING"},
    "RECONCILING": {"RECONCILED", "DISPUTED"},
    "RECONCILED": set(),
    "DISPUTED": {"UNDER_REVIEW", "REVERSED", "CANCELLED"},
    "REVERSED": set(),
    "CANCELLED": set(),
    "FAILED": {"DRAFT", "CANCELLED"},
}

# ---------------------------------------------------------------------------
# Settlement dispute
# ---------------------------------------------------------------------------
DISPUTE_TRANSITIONS = {
    "OPEN": {"UNDER_REVIEW", "RESOLVED", "REJECTED", "ESCALATED"},
    "UNDER_REVIEW": {"RESOLVED", "REJECTED", "ESCALATED"},
    "RESOLVED": set(),
    "REJECTED": set(),
    "ESCALATED": {"RESOLVED", "REJECTED"},
}

# ---------------------------------------------------------------------------
# Customer transfer
# ---------------------------------------------------------------------------
TRANSFER_TRANSITIONS = {
    "REQUESTED": {"VALIDATING", "CANCELLED"},
    "VALIDATING": {"APPROVED", "REJECTED", "CANCELLED"},
    "APPROVED": {"COMPLETED", "CANCELLED"},
    "COMPLETED": set(),
    "REJECTED": set(),
    "CANCELLED": set(),
}

_MAPS = {
    "tenant": TENANT_TRANSITIONS,
    "provisioning": PROVISIONING_TRANSITIONS,
    "partner": PARTNER_TRANSITIONS,
    "settlement": SETTLEMENT_TRANSITIONS,
    "dispute": DISPUTE_TRANSITIONS,
    "transfer": TRANSFER_TRANSITIONS,
}


def transition(kind: str, current: str, target: str) -> None:
    allowed = _MAPS[kind].get(current, set())
    if target not in allowed:
        raise ValueError(f"invalid transition: {current} -> {target}")


def tenant_transition(current, target):
    transition("tenant", current, target)


def provisioning_transition(current, target):
    transition("provisioning", current, target)


def partner_transition(current, target):
    transition("partner", current, target)


def settlement_transition(current, target):
    transition("settlement", current, target)


def dispute_transition(current, target):
    transition("dispute", current, target)


def transfer_transition(current, target):
    transition("transfer", current, target)


def guarded(kind: str, current: str, target: str):
    """Raise StateTransitionError instead of ValueError (service convenience)."""
    try:
        transition(kind, current, target)
    except ValueError as error:
        raise StateTransitionError(str(error)) from error
