"""Validated state machines for fraud, models, remediation and intents."""
from .domain.exceptions import StateTransitionError

FRAUD_SIGNAL_TRANSITIONS = {
    "OPEN": {"IN_REVIEW", "CLOSED"},
    "IN_REVIEW": {"APPROVED", "REJECTED", "CLOSED"},
    "APPROVED": {"ACTIONED", "CLOSED"},
    "REJECTED": {"CLOSED"},
    "ACTIONED": {"CLOSED"},
    "CLOSED": set(),
}

FRAUD_CASE_TRANSITIONS = {
    "OPEN": {"IN_REVIEW", "CLOSED"},
    "IN_REVIEW": {"APPROVED", "REJECTED", "CLOSED"},
    "APPROVED": {"ACTIONED", "CLOSED"},
    "REJECTED": {"CLOSED"},
    "ACTIONED": {"CLOSED"},
    "CLOSED": set(),
}

MODEL_TRANSITIONS = {
    "DRAFT": {"EVALUATED", "REJECTED"},
    "EVALUATED": {"PENDING_APPROVAL", "REJECTED", "DRAFT"},
    "PENDING_APPROVAL": {"APPROVED", "REJECTED", "DRAFT"},
    "APPROVED": {"SHADOW", "PRODUCTION", "RETIRED", "ROLLED_BACK"},
    "SHADOW": {"CANARY", "PRODUCTION", "ROLLED_BACK", "RETIRED"},
    "CANARY": {"PRODUCTION", "ROLLED_BACK", "RETIRED"},
    "PRODUCTION": {"SHADOW", "ROLLED_BACK", "RETIRED"},
    "ROLLED_BACK": {"RETIRED", "SHADOW"},
    "RETIRED": {"ARCHIVED"},
    "ARCHIVED": set(),
    "REJECTED": set(),
}

REMEDIATION_TRANSITIONS = {
    "PENDING": {"APPROVED", "REJECTED", "CANCELLED", "BLOCKED"},
    "APPROVED": {"STARTED", "CANCELLED", "BLOCKED", "COMPENSATED"},
    "REJECTED": set(),
    "BLOCKED": {"CANCELLED"},
    "STARTED": {"COMPLETED", "FAILED", "COMPENSATED"},
    "COMPLETED": set(),
    "FAILED": {"COMPENSATED", "CANCELLED"},
    "COMPENSATED": set(),
    "CANCELLED": set(),
}

RISK_STATE_TRANSITIONS = {
    "ACTIVE": {"EXPIRED", "ACTIONED"},
    "EXPIRED": set(),
    "ACTIONED": set(),
}

RECOMMENDATION_TRANSITIONS = {
    "OPEN": {"ACCEPTED", "REJECTED", "EXPIRED", "ACTIONED"},
    "ACCEPTED": {"ACTIONED", "EXPIRED"},
    "REJECTED": set(),
    "EXPIRED": set(),
    "ACTIONED": set(),
}

_MAPS = {
    "fraud_signal": FRAUD_SIGNAL_TRANSITIONS,
    "fraud_case": FRAUD_CASE_TRANSITIONS,
    "model": MODEL_TRANSITIONS,
    "remediation": REMEDIATION_TRANSITIONS,
    "risk": RISK_STATE_TRANSITIONS,
    "recommendation": RECOMMENDATION_TRANSITIONS,
}


def transition(kind: str, current: str, target: str) -> None:
    allowed = _MAPS[kind].get(current, set())
    if target not in allowed:
        raise ValueError(f"invalid transition: {current} -> {target}")


def guarded(kind: str, current: str, target: str):
    try:
        transition(kind, current, target)
    except ValueError as error:
        raise StateTransitionError(str(error)) from error
