"""Validated support ticket state machine (Milestone 5).

No code may mutate a ticket's status directly; every transition goes through
these functions so validation, immutable events, SLA timer effects and audit
are guaranteed. Arbitrary status PATCHes are not allowed — the API exposes
explicit domain commands that map to these transitions.
"""
from __future__ import annotations

TICKET_TRANSITIONS = {
    "NEW": {"TRIAGE", "ASSIGNED", "IN_PROGRESS", "PENDING_CUSTOMER", "PENDING_INTERNAL_TEAM", "PENDING_OSS_ORDER",
            "PENDING_FIELD_VISIT", "ESCALATED", "RESOLVED", "CANCELLED", "DUPLICATE"},
    "TRIAGE": {"ASSIGNED", "IN_PROGRESS", "PENDING_CUSTOMER", "PENDING_INTERNAL_TEAM",
               "PENDING_VENDOR", "PENDING_FIELD_VISIT", "PENDING_OSS_ORDER", "PENDING_BILLING_ACTION",
               "ESCALATED", "RESOLVED", "REOPENED", "CANCELLED", "DUPLICATE"},
    "ASSIGNED": {"IN_PROGRESS", "PENDING_CUSTOMER", "PENDING_INTERNAL_TEAM", "PENDING_VENDOR",
                 "PENDING_FIELD_VISIT", "PENDING_OSS_ORDER", "PENDING_BILLING_ACTION",
                 "ESCALATED", "RESOLVED", "CANCELLED", "DUPLICATE"},
    "IN_PROGRESS": {"PENDING_CUSTOMER", "PENDING_INTERNAL_TEAM", "PENDING_VENDOR", "PENDING_FIELD_VISIT",
                    "PENDING_OSS_ORDER", "PENDING_BILLING_ACTION", "ESCALATED", "RESOLVED",
                    "REOPENED", "CANCELLED", "DUPLICATE"},
    "PENDING_CUSTOMER": {"IN_PROGRESS", "REOPENED", "ESCALATED", "RESOLVED", "CANCELLED", "DUPLICATE"},
    "PENDING_INTERNAL_TEAM": {"IN_PROGRESS", "PENDING_CUSTOMER", "PENDING_VENDOR", "PENDING_FIELD_VISIT",
                              "PENDING_OSS_ORDER", "PENDING_BILLING_ACTION", "ESCALATED", "RESOLVED", "REOPENED"},
    "PENDING_VENDOR": {"IN_PROGRESS", "PENDING_CUSTOMER", "ESCALATED", "RESOLVED", "REOPENED"},
    "PENDING_FIELD_VISIT": {"IN_PROGRESS", "PENDING_CUSTOMER", "ESCALATED", "RESOLVED", "REOPENED"},
    "PENDING_OSS_ORDER": {"IN_PROGRESS", "PENDING_CUSTOMER", "ESCALATED", "RESOLVED", "REOPENED"},
    "PENDING_BILLING_ACTION": {"IN_PROGRESS", "PENDING_CUSTOMER", "ESCALATED", "RESOLVED", "REOPENED"},
    "ESCALATED": {"IN_PROGRESS", "ASSIGNED", "TRIAGE", "PENDING_CUSTOMER", "RESOLVED", "REOPENED", "CANCELLED"},
    "RESOLVED": {"CLOSED", "REOPENED", "CANCELLED", "DUPLICATE"},
    "CLOSED": {"REOPENED"},
    "REOPENED": {"TRIAGE", "ASSIGNED", "IN_PROGRESS", "PENDING_CUSTOMER", "PENDING_INTERNAL_TEAM",
                 "ESCALATED", "RESOLVED", "CANCELLED", "DUPLICATE"},
    "CANCELLED": set(),
    "DUPLICATE": {"REOPENED"},
}

# Domain commands -> allowed resulting states. Commands keep lifecycle explicit.
COMMAND_STATES = {
    "assign": {"ASSIGNED"},
    "accept": {"IN_PROGRESS"},
    "start_work": {"IN_PROGRESS"},
    "request_customer_info": {"PENDING_CUSTOMER"},
    "transfer_queue": {"ASSIGNED", "TRIAGE"},
    "escalate": {"ESCALATED"},
    "link_outage": set(),  # state-agnostic; records relationship + event
    "create_field_job": {"PENDING_FIELD_VISIT"},
    "create_oss_order": {"PENDING_OSS_ORDER"},
    "resolve": {"RESOLVED"},
    "confirm_resolution": {"CLOSED"},
    "close": {"CLOSED"},
    "reopen": {"REOPENED"},
    "cancel": {"CANCELLED"},
    "mark_duplicate": {"DUPLICATE"},
}


def ticket_transition(current: str, target: str) -> str:
    if current not in TICKET_TRANSITIONS:
        raise ValueError(f"unknown ticket state {current!r}")
    if target not in TICKET_TRANSITIONS[current]:
        raise ValueError(f"invalid ticket transition: {current} -> {target}")
    return target


def command_transition(current: str, command: str) -> str:
    """Resolve the target state a command requests from a given current state."""
    targets = COMMAND_STATES.get(command, set())
    # link_outage etc. don't change state
    if not targets:
        return current
    for target in targets:
        if target in TICKET_TRANSITIONS.get(current, set()):
            return target
    raise ValueError(f"invalid ticket command {command!r} from state {current!r}")


def ticket_terminal(state: str) -> bool:
    return not TICKET_TRANSITIONS.get(state, set())


def customer_status(internal: str) -> str:
    """Customer-visible status derived from the internal state."""
    from .enums import CUSTOMER_STATUS_MAP

    return CUSTOMER_STATUS_MAP.get(internal, "SUBMITTED")
