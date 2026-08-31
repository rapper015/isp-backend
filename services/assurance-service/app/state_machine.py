"""Validated state machines for alerts, incidents, maintenance windows,
postmortems and action items."""
from .domain.exceptions import StateTransitionError

ALERT_TRANSITIONS = {
    "PENDING": {"FIRING", "SUPPRESSED", "SILENCED", "RESOLVED", "EXPIRED"},
    "FIRING": {"ACKNOWLEDGED", "SUPPRESSED", "SILENCED", "RESOLVED", "EXPIRED", "PENDING"},
    "ACKNOWLEDGED": {"SUPPRESSED", "SILENCED", "RESOLVED", "EXPIRED", "FIRING"},
    "SUPPRESSED": {"FIRING", "SILENCED", "RESOLVED", "EXPIRED"},
    "SILENCED": {"FIRING", "RESOLVED", "EXPIRED"},
    "RESOLVED": set(),
    "EXPIRED": set(),
}

INCIDENT_TRANSITIONS = {
    "DETECTED": {"TRIAGE", "INVESTIGATING", "IDENTIFIED", "MITIGATING", "MONITORING", "POSTMORTEM_REQUIRED"},
    "TRIAGE": {"INVESTIGATING", "IDENTIFIED", "MITIGATING", "MONITORING", "POSTMORTEM_REQUIRED"},
    "INVESTIGATING": {"IDENTIFIED", "MITIGATING", "MONITORING", "POSTMORTEM_REQUIRED"},
    "IDENTIFIED": {"MITIGATING", "MONITORING", "POSTMORTEM_REQUIRED"},
    "MITIGATING": {"MONITORING", "POSTMORTEM_REQUIRED"},
    "MONITORING": {"RESOLVED", "POSTMORTEM_REQUIRED"},
    "RESOLVED": {"CLOSED", "POSTMORTEM_REQUIRED"},
    "POSTMORTEM_REQUIRED": {"CLOSED"},
    "CLOSED": set(),
}

MAINTENANCE_TRANSITIONS = {
    "REQUESTED": {"APPROVED", "REJECTED", "CANCELLED"},
    "APPROVED": {"ACTIVE", "CANCELLED", "COMPLETED"},
    "REJECTED": set(),
    "CANCELLED": set(),
    "ACTIVE": {"COMPLETED", "CANCELLED"},
    "COMPLETED": set(),
}

POSTMORTEM_TRANSITIONS = {
    "DRAFT": {"REVIEWING", "APPROVED", "CANCELLED"},
    "REVIEWING": {"APPROVED", "CANCELLED"},
    "APPROVED": set(),
    "CANCELLED": set(),
}

ACTION_ITEM_TRANSITIONS = {
    "OPEN": {"IN_PROGRESS", "DONE", "CANCELLED"},
    "IN_PROGRESS": {"DONE", "CANCELLED"},
    "DONE": {"REOPENED"},
    "REOPENED": {"IN_PROGRESS", "DONE"},
    "CANCELLED": set(),
}

_MAPS = {
    "alert": ALERT_TRANSITIONS,
    "incident": INCIDENT_TRANSITIONS,
    "maintenance": MAINTENANCE_TRANSITIONS,
    "postmortem": POSTMORTEM_TRANSITIONS,
    "action_item": ACTION_ITEM_TRANSITIONS,
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
