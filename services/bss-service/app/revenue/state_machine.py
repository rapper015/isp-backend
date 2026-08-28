"""Validated BSS revenue state machines. No arbitrary status patching."""
from __future__ import annotations

PAYMENT_INTENT_TRANSITIONS = {
    "CREATED": {"PENDING", "CANCELLED", "EXPIRED"},
    "PENDING": {"REQUIRES_ACTION", "PROCESSING", "PARTIALLY_PAID", "PAID", "FAILED", "EXPIRED", "CANCELLED"},
    "REQUIRES_ACTION": {"PROCESSING", "PARTIALLY_PAID", "PAID", "FAILED", "EXPIRED", "CANCELLED"},
    "PROCESSING": {"PARTIALLY_PAID", "PAID", "FAILED", "EXPIRED", "CANCELLED"},
    "PARTIALLY_PAID": {"PROCESSING", "PAID", "EXPIRED"},
    "PAID": set(),
    "FAILED": {"PENDING"},
    "EXPIRED": set(),
    "CANCELLED": set(),
}

PAYMENT_ATTEMPT_TRANSITIONS = {
    "CREATED": {"SUBMITTED", "CANCELLED", "EXPIRED"},
    "SUBMITTED": {"AUTHORIZED", "FAILED", "CANCELLED", "EXPIRED"},
    "AUTHORIZED": {"CAPTURE_PENDING", "FAILED", "CANCELLED"},
    "CAPTURE_PENDING": {"CAPTURED", "FAILED"},
    "CAPTURED": {"REFUND_PENDING", "PARTIALLY_REFUNDED", "REFUNDED", "DISPUTED", "CHARGEBACK"},
    "REFUND_PENDING": {"PARTIALLY_REFUNDED", "REFUNDED", "FAILED"},
    "PARTIALLY_REFUNDED": {"REFUND_PENDING", "REFUNDED"},
    "REFUNDED": set(),
    "FAILED": {"SUBMITTED"},
    "CANCELLED": set(),
    "EXPIRED": set(),
    "DISPUTED": {"CHARGEBACK", "REFUNDED"},
    "CHARGEBACK": set(),
}

MANUAL_PAYMENT_TRANSITIONS = {
    "DRAFT": {"SUBMITTED", "CANCELLED", "REJECTED"},
    "SUBMITTED": {"UNDER_REVIEW", "APPROVED", "REJECTED", "CANCELLED"},
    "UNDER_REVIEW": {"APPROVED", "REJECTED", "SUBMITTED"},
    "APPROVED": {"POSTED", "REJECTED"},
    "POSTED": {"REVERSED"},
    "REJECTED": set(),
    "REVERSED": set(),
    "CANCELLED": set(),
}

RECONCILIATION_ITEM_TRANSITIONS = {
    "UNMATCHED": {"MATCHED", "PARTIALLY_MATCHED", "EXCEPTION", "MANUAL_REVIEW"},
    "PARTIALLY_MATCHED": {"MATCHED", "EXCEPTION", "MANUAL_REVIEW"},
    "MATCHED": {"RESOLVED"},
    "MISMATCH": {"EXCEPTION", "MANUAL_REVIEW", "RESOLVED"},
    "EXCEPTION": {"MANUAL_REVIEW", "RESOLVED"},
    "MANUAL_REVIEW": {"MATCHED", "EXCEPTION", "RESOLVED"},
    "RESOLVED": set(),
}

DUNNING_CASE_TRANSITIONS = {
    "OPEN": {"PAUSED", "RESOLVED", "CLOSED"},
    "PAUSED": {"OPEN", "RESOLVED", "CLOSED"},
    "RESOLVED": set(),
    "CLOSED": {"OPEN"},
}


def _transition(name: str, table: dict, current: str, target: str) -> str:
    if target not in table.get(current, set()):
        raise ValueError(f"invalid {name} transition: {current} -> {target}")
    return target


def intent_transition(current: str, target: str) -> str:
    return _transition("payment intent", PAYMENT_INTENT_TRANSITIONS, current, target)


def attempt_transition(current: str, target: str) -> str:
    return _transition("payment attempt", PAYMENT_ATTEMPT_TRANSITIONS, current, target)


def manual_payment_transition(current: str, target: str) -> str:
    return _transition("manual payment", MANUAL_PAYMENT_TRANSITIONS, current, target)


def recon_item_transition(current: str, target: str) -> str:
    return _transition("reconciliation item", RECONCILIATION_ITEM_TRANSITIONS, current, target)


def dunning_case_transition(current: str, target: str) -> str:
    return _transition("dunning case", DUNNING_CASE_TRANSITIONS, current, target)
