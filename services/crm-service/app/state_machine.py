"""Explicit CRM state machines. No code may mutate a status field directly;
transitions must go through these validated functions so rules, audit and
events are guaranteed.

Four machines are defined:
* LEAD_STAGES        - lead pipeline
* CUSTOMER_LIFECYCLE - customer lifecycle
* KYC                - KYC case state
* CAF                - Customer Application Form state
"""
from __future__ import annotations

from .enums import CAF_STATUSES, CUSTOMER_LIFECYCLE, KYC_STATUSES, LEAD_STAGES

# ---------------------------------------------------------------------------
# Lead pipeline
# ---------------------------------------------------------------------------

LEAD_TRANSITIONS = {
    "NEW": {"ASSIGNED", "CONTACTED", "QUALIFICATION", "DISQUALIFIED", "DUPLICATE", "LOST"},
    "ASSIGNED": {"CONTACTED", "QUALIFICATION", "DISQUALIFIED", "DUPLICATE", "LOST"},
    "CONTACTED": {"QUALIFICATION", "FEASIBILITY_PENDING", "PROPOSAL_SENT", "DISQUALIFIED", "DUPLICATE", "LOST"},
    "QUALIFICATION": {"FEASIBILITY_PENDING", "FEASIBLE", "NOT_FEASIBLE", "PROPOSAL_SENT", "KYC_PENDING", "DISQUALIFIED", "DUPLICATE", "LOST"},
    "FEASIBILITY_PENDING": {"FEASIBLE", "NOT_FEASIBLE", "DISQUALIFIED", "DUPLICATE", "LOST"},
    "FEASIBLE": {"PROPOSAL_SENT", "KYC_PENDING", "WON", "LOST", "DISQUALIFIED"},
    "NOT_FEASIBLE": {"LOST", "DISQUALIFIED"},
    "PROPOSAL_SENT": {"NEGOTIATION", "WON", "LOST", "KYC_PENDING"},
    "NEGOTIATION": {"WON", "LOST", "KYC_PENDING", "DISQUALIFIED"},
    "KYC_PENDING": {"WON", "CONVERTED", "LOST", "DISQUALIFIED"},
    "WON": {"CONVERTED", "LOST"},
    "CONVERTED": set(),
    "LOST": {"NEW", "QUALIFICATION"},
    "DISQUALIFIED": {"NEW", "QUALIFICATION"},
    "DUPLICATE": {"NEW", "QUALIFICATION"},
}
# Canonical lead stages are exported from enums.
LEAD_STAGES = LEAD_STAGES

def lead_transition(current: str, target: str) -> str:
    if target not in LEAD_TRANSITIONS.get(current, set()):
        raise ValueError(f"invalid lead stage transition: {current} -> {target}")
    return target


def lead_terminal(state: str) -> bool:
    return not LEAD_TRANSITIONS.get(state, set())


# ---------------------------------------------------------------------------
# Customer lifecycle
# ---------------------------------------------------------------------------

CUSTOMER_TRANSITIONS = {
    "PROSPECT": {"ONBOARDING", "CLOSED"},
    "ONBOARDING": {"KYC_PENDING", "KYC_REJECTED", "READY_FOR_SERVICE", "TERMINATED", "CLOSED"},
    "KYC_PENDING": {"KYC_VERIFIED", "KYC_REJECTED", "ONBOARDING", "CLOSED"},
    "KYC_REJECTED": {"KYC_PENDING", "CLOSED", "TERMINATED"},
    "KYC_VERIFIED": {"READY_FOR_SERVICE", "ACTIVATION_PENDING", "CLOSED"},
    "READY_FOR_SERVICE": {"ACTIVATION_PENDING", "ACTIVE", "TERMINATED", "CLOSED"},
    "ACTIVATION_PENDING": {"ACTIVE", "SUSPENDED", "TERMINATED"},
    "ACTIVE": {"SUSPENSION_PENDING", "TERMINATION_PENDING", "SUSPENDED", "REACTIVATION_PENDING"},
    "SUSPENSION_PENDING": {"SUSPENDED", "ACTIVE", "TERMINATED"},
    "SUSPENDED": {"REACTIVATION_PENDING", "TERMINATION_PENDING", "ACTIVE", "TERMINATED"},
    "REACTIVATION_PENDING": {"ACTIVE", "TERMINATED"},
    "TERMINATION_PENDING": {"TERMINATED", "ACTIVE"},
    "TERMINATED": {"CLOSED"},
    "CLOSED": set(),
}


def lifecycle_transition(current: str, target: str) -> str:
    if target not in CUSTOMER_TRANSITIONS.get(current, set()):
        raise ValueError(f"invalid customer lifecycle transition: {current} -> {target}")
    return target


def lifecycle_terminal(state: str) -> bool:
    return not CUSTOMER_TRANSITIONS.get(state, set())


# ---------------------------------------------------------------------------
# KYC
# ---------------------------------------------------------------------------

KYC_TRANSITIONS = {
    "NOT_STARTED": {"DRAFT", "SUBMITTED"},
    "DRAFT": {"SUBMITTED", "NOT_STARTED", "CANCELLED"},
    "SUBMITTED": {"UNDER_REVIEW", "ADDITIONAL_INFO_REQUIRED", "VERIFIED", "REJECTED"},
    "UNDER_REVIEW": {"VERIFIED", "REJECTED", "ADDITIONAL_INFO_REQUIRED"},
    "ADDITIONAL_INFO_REQUIRED": {"SUBMITTED", "DRAFT"},
    "VERIFIED": {"EXPIRED", "REVOKED"},
    "REJECTED": {"DRAFT", "SUBMITTED"},
    "EXPIRED": {"SUBMITTED", "DRAFT"},
    "REVOKED": set(),
    "CANCELLED": set(),
}


def kyc_transition(current: str, target: str) -> str:
    if target not in KYC_TRANSITIONS.get(current, set()):
        raise ValueError(f"invalid KYC transition: {current} -> {target}")
    return target


# ---------------------------------------------------------------------------
# CAF
# ---------------------------------------------------------------------------

CAF_TRANSITIONS = {
    "DRAFT": {"SUBMITTED", "CANCELLED"},
    "SUBMITTED": {"UNDER_REVIEW", "INCOMPLETE", "VERIFIED", "APPROVED", "REJECTED"},
    "INCOMPLETE": {"SUBMITTED", "DRAFT"},
    "UNDER_REVIEW": {"VERIFIED", "APPROVED", "REJECTED", "INCOMPLETE"},
    "VERIFIED": {"APPROVED", "REJECTED"},
    "APPROVED": {"SUPERSEDED"},
    "REJECTED": {"DRAFT", "SUBMITTED"},
    "CANCELLED": {"DRAFT"},
    "SUPERSEDED": set(),
}


def caf_transition(current: str, target: str) -> str:
    if target not in CAF_TRANSITIONS.get(current, set()):
        raise ValueError(f"invalid CAF transition: {current} -> {target}")
    return target


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_LIFECYCLE = set(CUSTOMER_LIFECYCLE)
VALID_LEAD_STAGES = set(LEAD_STAGES)
VALID_KYC = set(KYC_STATUSES)
VALID_CAF = set(CAF_STATUSES)


def ensure_valid(values: set[str], value: str, label: str) -> str:
    if value not in values:
        raise ValueError(f"invalid {label}: {value}")
    return value
