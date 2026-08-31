"""Validated OSS state machines: order lifecycle and saga/step lifecycle.

No code may mutate an order's state directly; transitions must go through these
functions so audit, events and projections are guaranteed.
"""
from __future__ import annotations

ORDER_TRANSITIONS = {
    "DRAFT": {"SUBMITTED", "CANCELLED"},
    "SUBMITTED": {"VALIDATING", "CANCELLATION_REQUESTED", "VALIDATION_FAILED"},
    "VALIDATING": {"PAYMENT_PENDING", "READY_FOR_FULFILMENT", "VALIDATION_FAILED", "CANCELLATION_REQUESTED"},
    "VALIDATION_FAILED": {"SUBMITTED", "CANCELLED"},
    "PAYMENT_PENDING": {"READY_FOR_FULFILMENT", "VALIDATION_FAILED", "CANCELLATION_REQUESTED"},
    "READY_FOR_FULFILMENT": {"RESOURCE_RESERVATION", "PROVISIONING", "CANCELLATION_REQUESTED", "MANUAL_INTERVENTION_REQUIRED", "FAILED", "COMPENSATING"},
    "RESOURCE_RESERVATION": {"FIELD_INSTALLATION_PENDING", "PROVISIONING", "FAILED", "CANCELLATION_REQUESTED", "COMPENSATING"},
    "FIELD_INSTALLATION_PENDING": {"PROVISIONING", "FAILED", "MANUAL_INTERVENTION_REQUIRED", "COMPENSATING"},
    "PROVISIONING": {"VERIFYING", "FAILED", "COMPENSATING"},
    "VERIFYING": {"COMPLETED", "FAILED", "COMPENSATING"},
    "COMPLETED": set(),
    "FAILED": {"MANUAL_INTERVENTION_REQUIRED", "COMPENSATING", "SUBMITTED"},
    "COMPENSATING": {"ROLLED_BACK", "FAILED", "MANUAL_INTERVENTION_REQUIRED"},
    "ROLLED_BACK": set(),
    "CANCELLATION_REQUESTED": {"CANCELLED", "COMPENSATING", "SUBMITTED"},
    "CANCELLED": set(),
    "MANUAL_INTERVENTION_REQUIRED": {"SUBMITTED", "PROVISIONING", "CANCELLED", "COMPENSATING"},
}

SAGA_TRANSITIONS = {
    "PENDING": {"RUNNING", "CANCELLED"},
    "RUNNING": {"COMPLETED", "FAILED", "COMPENSATING", "MANUAL_INTERVENTION", "TIMED_OUT"},
    "COMPENSATING": {"COMPENSATED", "FAILED", "MANUAL_INTERVENTION"},
    "COMPLETED": set(),
    "FAILED": {"MANUAL_INTERVENTION", "RUNNING"},
    "COMPENSATED": set(),
    "MANUAL_INTERVENTION": {"RUNNING", "COMPENSATING", "CANCELLED"},
    "TIMED_OUT": {"RUNNING", "MANUAL_INTERVENTION"},
    "CANCELLED": set(),
}

STEP_TRANSITIONS = {
    "PENDING": {"RUNNING", "SKIPPED"},
    "RUNNING": {"COMPLETED", "FAILED", "COMPENSATED"},
    "COMPLETED": {"COMPENSATED"},
    "FAILED": {"RUNNING", "COMPENSATED", "SKIPPED"},
    "COMPENSATED": set(),
    "SKIPPED": set(),
}


def order_transition(current: str, target: str) -> str:
    if target not in ORDER_TRANSITIONS.get(current, set()):
        raise ValueError(f"invalid order transition: {current} -> {target}")
    return target


def order_terminal(state: str) -> bool:
    return not ORDER_TRANSITIONS.get(state, set())


def saga_transition(current: str, target: str) -> str:
    if target not in SAGA_TRANSITIONS.get(current, set()):
        raise ValueError(f"invalid saga transition: {current} -> {target}")
    return target


def step_transition(current: str, target: str) -> str:
    if target not in STEP_TRANSITIONS.get(current, set()):
        raise ValueError(f"invalid saga step transition: {current} -> {target}")
    return target
