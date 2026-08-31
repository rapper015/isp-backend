"""Priority / severity calculation from impact and urgency.

Priority, severity, impact and urgency are distinct concepts and are never
collapsed into one generic field. Priority is derived from a configurable
impact x urgency matrix; severity is an operational classification of
service-affecting damage. Authorized overrides require a reason and an audit
event (enforced by the ticket service)."""
from __future__ import annotations

from ..enums import IMPACT_LEVELS, PRIORITY_MATRIX, PRIORITIES, URGENCY_LEVELS

SEVERITY_BY_PRIORITY = {
    "P1_CRITICAL": "SEV1",
    "P2_HIGH": "SEV2",
    "P3_MEDIUM": "SEV3",
    "P4_LOW": "SEV4",
}


def calculate_priority(impact: str, urgency: str) -> str:
    impact = (impact or "MEDIUM").upper()
    urgency = (urgency or "MEDIUM").upper()
    if impact not in IMPACT_LEVELS:
        raise ValueError(f"invalid impact {impact!r}")
    if urgency not in URGENCY_LEVELS:
        raise ValueError(f"invalid urgency {urgency!r}")
    return PRIORITY_MATRIX[(impact, urgency)]


def priority_rank(priority: str) -> int:
    try:
        return PRIORITIES.index(priority)
    except ValueError:
        raise ValueError(f"invalid priority {priority!r}") from None


def severity_for_priority(priority: str) -> str:
    return SEVERITY_BY_PRIORITY.get(priority, "SEV3")


def is_higher_priority(a: str, b: str) -> bool:
    """True when priority a is more urgent than priority b."""
    return priority_rank(a) < priority_rank(b)


def validate_priority(priority: str) -> str:
    priority = (priority or "P3_MEDIUM").upper()
    if priority not in PRIORITIES:
        raise ValueError(f"invalid priority {priority!r}")
    return priority
