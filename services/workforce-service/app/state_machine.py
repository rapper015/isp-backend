"""Validated workforce state machines: work-order lifecycle, appointment
lifecycle and field-visit lifecycle.

No code may mutate a work order's state directly; every change goes through
these functions so validation, immutable events, SLA effects and audit are
guaranteed. There are no arbitrary status-update endpoints."""
from __future__ import annotations

WORK_ORDER_TRANSITIONS = {
    "DRAFT": {"CREATED", "CANCELLED"},
    "CREATED": {"VALIDATING", "CANCELLED", "FAILED"},
    "VALIDATING": {"READY_FOR_SCHEDULING", "FAILED", "CANCELLED"},
    "READY_FOR_SCHEDULING": {"SCHEDULED", "RESCHEDULE_REQUIRED", "CANCELLED"},
    "SCHEDULED": {"ASSIGNED", "RESCHEDULE_REQUIRED", "CANCELLED", "CUSTOMER_UNAVAILABLE"},
    "ASSIGNED": {"DISPATCHED", "READY_FOR_SCHEDULING", "RESCHEDULE_REQUIRED", "CANCELLED", "BLOCKED", "CUSTOMER_UNAVAILABLE"},
    "DISPATCHED": {"EN_ROUTE", "ARRIVED", "IN_PROGRESS", "RESCHEDULE_REQUIRED", "CANCELLED", "BLOCKED", "CUSTOMER_UNAVAILABLE"},
    "EN_ROUTE": {"ARRIVED", "IN_PROGRESS", "RESCHEDULE_REQUIRED", "BLOCKED", "CANCELLED", "CUSTOMER_UNAVAILABLE"},
    "ARRIVED": {"IN_PROGRESS", "BLOCKED", "CUSTOMER_UNAVAILABLE", "RESCHEDULE_REQUIRED", "PAUSED"},
    "IN_PROGRESS": {"PAUSED", "BLOCKED", "AWAITING_PARTS", "AWAITING_REMOTE_ACTION", "CUSTOMER_UNAVAILABLE",
                    "EXECUTION_COMPLETED", "RESCHEDULE_REQUIRED", "FAILED"},
    "PAUSED": {"IN_PROGRESS", "BLOCKED", "AWAITING_PARTS", "AWAITING_REMOTE_ACTION", "CUSTOMER_UNAVAILABLE",
               "RESCHEDULE_REQUIRED", "FAILED"},
    "BLOCKED": {"IN_PROGRESS", "AWAITING_PARTS", "AWAITING_REMOTE_ACTION", "CUSTOMER_UNAVAILABLE",
                "RESCHEDULE_REQUIRED", "FAILED", "CANCELLED"},
    "CUSTOMER_UNAVAILABLE": {"RESCHEDULE_REQUIRED", "IN_PROGRESS", "BLOCKED", "CANCELLED"},
    "AWAITING_PARTS": {"IN_PROGRESS", "BLOCKED", "RESCHEDULE_REQUIRED", "FAILED"},
    "AWAITING_REMOTE_ACTION": {"IN_PROGRESS", "BLOCKED", "AWAITING_PARTS", "FAILED"},
    "RESCHEDULE_REQUIRED": {"READY_FOR_SCHEDULING", "SCHEDULED", "ASSIGNED", "CANCELLED"},
    "EXECUTION_COMPLETED": {"VERIFICATION_PENDING", "QA_REJECTED", "COMPLETED", "FAILED"},
    "VERIFICATION_PENDING": {"COMPLETED", "QA_REJECTED", "FAILED"},
    "QA_REJECTED": {"ASSIGNED", "IN_PROGRESS", "EXECUTION_COMPLETED", "RESCHEDULE_REQUIRED", "CANCELLED", "FAILED"},
    "COMPLETED": set(),
    "FAILED": set(),
    "CANCELLED": set(),
}

# Domain commands -> resulting state. Commands keep lifecycle explicit.
WORK_ORDER_COMMANDS = {
    "validate": "READY_FOR_SCHEDULING",
    "schedule": "SCHEDULED",
    "reschedule": "RESCHEDULE_REQUIRED",
    "assign": "ASSIGNED",
    "accept_assignment": "ASSIGNED",
    "reject_assignment": "READY_FOR_SCHEDULING",
    "dispatch": "DISPATCHED",
    "start_travel": "EN_ROUTE",
    "check_in": "ARRIVED",
    "start_work": "IN_PROGRESS",
    "pause": "PAUSED",
    "record_blocker": "BLOCKED",
    "request_assistance": "BLOCKED",
    "request_parts": "AWAITING_PARTS",
    "resume": "IN_PROGRESS",
    "finish_execution": "EXECUTION_COMPLETED",
    "submit_for_verification": "VERIFICATION_PENDING",
    "approve": "COMPLETED",
    "reject": "QA_REJECTED",
    "complete": "COMPLETED",
    "fail": "FAILED",
    "cancel": "CANCELLED",
}

APPOINTMENT_TRANSITIONS = {
    "PROPOSED": {"CUSTOMER_CONFIRMATION_PENDING", "CONFIRMED", "CANCELLED", "RESCHEDULED"},
    "CUSTOMER_CONFIRMATION_PENDING": {"CONFIRMED", "TECHNICIAN_DISPATCHED", "RESCHEDULED", "CANCELLED"},
    "CONFIRMED": {"TECHNICIAN_DISPATCHED", "TECHNICIAN_ARRIVED", "RESCHEDULED", "CANCELLED",
                  "CUSTOMER_NO_SHOW", "TECHNICIAN_NO_SHOW", "COMPLETED"},
    "RESCHEDULED": {"PROPOSED", "CUSTOMER_CONFIRMATION_PENDING", "CONFIRMED", "CANCELLED"},
    "TECHNICIAN_DISPATCHED": {"TECHNICIAN_ARRIVED", "COMPLETED", "CUSTOMER_NO_SHOW", "CANCELLED"},
    "TECHNICIAN_ARRIVED": {"COMPLETED", "CUSTOMER_NO_SHOW", "CANCELLED"},
    "COMPLETED": set(),
    "CUSTOMER_NO_SHOW": {"RESCHEDULED", "CANCELLED"},
    "TECHNICIAN_NO_SHOW": {"RESCHEDULED", "CANCELLED"},
    "CANCELLED": set(),
}

VISIT_TRANSITIONS = {
    "PLANNED": {"EN_ROUTE", "ON_SITE", "IN_PROGRESS", "ABANDONED"},
    "EN_ROUTE": {"ON_SITE", "IN_PROGRESS", "ABANDONED"},
    "ON_SITE": {"IN_PROGRESS", "PAUSED", "ABANDONED"},
    "IN_PROGRESS": {"PAUSED", "COMPLETED", "ABANDONED"},
    "PAUSED": {"IN_PROGRESS", "ABANDONED"},
    "COMPLETED": set(),
    "ABANDONED": set(),
}


def work_order_transition(current: str, target: str) -> str:
    if current not in WORK_ORDER_TRANSITIONS:
        raise ValueError(f"unknown work-order state {current!r}")
    if target not in WORK_ORDER_TRANSITIONS[current]:
        raise ValueError(f"invalid work-order transition: {current} -> {target}")
    return target


def work_order_command(current: str, command: str) -> str:
    """Resolve the target state a command requests from a given current state."""
    target = WORK_ORDER_COMMANDS.get(command)
    if target is None:
        raise ValueError(f"unknown work-order command {command!r}")
    if target in WORK_ORDER_TRANSITIONS.get(current, set()):
        return target
    raise ValueError(f"invalid work-order command {command!r} from state {current!r}")


def appointment_transition(current: str, target: str) -> str:
    if current not in APPOINTMENT_TRANSITIONS:
        raise ValueError(f"unknown appointment state {current!r}")
    if target not in APPOINTMENT_TRANSITIONS[current]:
        raise ValueError(f"invalid appointment transition: {current} -> {target}")
    return target


def visit_transition(current: str, target: str) -> str:
    if current not in VISIT_TRANSITIONS:
        raise ValueError(f"unknown visit state {current!r}")
    if target not in VISIT_TRANSITIONS[current]:
        raise ValueError(f"invalid visit transition: {current} -> {target}")
    return target
