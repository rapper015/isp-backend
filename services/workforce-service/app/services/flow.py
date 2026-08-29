"""Centralized validated work-order transition flow.

Every command that changes a work order's state goes through
``transition_work_order`` so state validation, immutable events, SLA timer
effects, timestamps and outbox effects are guaranteed. No code mutates a work
order's status directly."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..domain.exceptions import StateTransitionError
from ..domain.sla import engine as sla_engine
from ..models import WorkOrder
from ..state_machine import work_order_transition
from .audit_service import append_event

_PAUSE_STATES = {"CUSTOMER_UNAVAILABLE", "AWAITING_PARTS", "AWAITING_REMOTE_ACTION", "RESCHEDULE_REQUIRED"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _apply_sla_state_effect(session: Session, work_order: WorkOrder, previous: str, target: str) -> None:
    from . import sla_service

    sla = sla_service.get_field_sla(session, work_order)
    if sla is None:
        return
    pause_states = (sla.policy_snapshot.get("definition") or {}).get("pause_on_states", _PAUSE_STATES)
    if previous not in pause_states and target in pause_states:
        if sla_engine.pause_field_sla(session, sla, reason=f"state {target}", policy_rule="pause_on_states"):
            append_event(session, work_order, "work_order.sla_paused",
                         payload={"from_state": previous, "to_state": target}, actor_type="system",
                         actor_id="sla-engine", correlation_id=work_order.correlation_id)
    elif previous in pause_states and target not in pause_states:
        if sla_engine.resume_field_sla(session, sla):
            append_event(session, work_order, "work_order.sla_resumed",
                         payload={"from_state": previous, "to_state": target}, actor_type="system",
                         actor_id="sla-engine", correlation_id=work_order.correlation_id)
    work_order.field_sla_status = sla.status
    work_order.arrival_deadline = sla.arrival_deadline
    work_order.completion_deadline = sla.completion_deadline


def transition_work_order(
    session: Session,
    tenant_id,
    work_order: WorkOrder,
    target: str,
    *,
    event_type: str,
    payload: dict | None = None,
    actor: str = "system",
    actor_type: str = "agent",
    correlation_id: str | None = None,
    causation_id: str | None = None,
) -> WorkOrder:
    try:
        work_order_transition(work_order.status, target)
    except ValueError as error:
        raise StateTransitionError(str(error)) from error
    previous = work_order.status
    work_order.status = target
    if target == "COMPLETED":
        work_order.completed_at = _now()
    elif target == "FAILED":
        work_order.failed_at = _now()
    elif target == "CANCELLED":
        work_order.cancelled_at = _now()
    _apply_sla_state_effect(session, work_order, previous, target)
    append_event(session, work_order, event_type,
                 payload={"from": previous, "to": target, **(payload or {})},
                 actor_type=actor_type, actor_id=actor,
                 correlation_id=correlation_id or work_order.correlation_id, causation_id=causation_id)
    session.flush()
    return work_order
