"""Appointment service: windows, confirmation, rescheduling (history preserved),
reminders and no-show handling.

Appointment state is separate from work-order state. Rescheduling creates a new
appointment record and marks the previous one RESCHEDULED — history is never
erased."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.exceptions import ValidationError
from ..integrations.base import get_adapter
from ..models import Appointment, WorkOrder
from ..state_machine import appointment_transition
from . import technician_service  # noqa: F401
from .audit_service import append_event, correlation, outbox
from .flow import transition_work_order


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_appointment_or_404(session: Session, tenant_id, appointment_id: uuid.UUID) -> Appointment:
    appointment = session.get(Appointment, appointment_id)
    if appointment is None or appointment.tenant_id != tenant_id:
        from ..domain.exceptions import NotFoundError

        raise NotFoundError("appointment not found")
    return appointment


def schedule(session: Session, tenant_id, work_order: WorkOrder, *, window_start: datetime, window_end: datetime,
             customer_preferred: bool = False, actor: str | None = None, correlation_id: str | None = None) -> Appointment:
    if window_end <= window_start:
        raise ValidationError("appointment window end must be after start")
    attempts = session.scalar(select(Appointment.attempt_number).where(
        Appointment.work_order_id == work_order.id).order_by(Appointment.attempt_number.desc())) or 0
    appointment = Appointment(
        tenant_id=tenant_id, work_order_id=work_order.id, window_start=window_start, window_end=window_end,
        status="PROPOSED", attempt_number=attempts + 1, customer_preferred=customer_preferred,
        correlation_id=correlation_id or correlation(None),
    )
    session.add(appointment)
    session.flush()
    work_order.current_appointment_id = appointment.id
    work_order.scheduled_start = window_start
    work_order.scheduled_end = window_end
    transition_work_order(session, tenant_id, work_order, "SCHEDULED", event_type="work_order.scheduled",
                          payload={"appointment_id": str(appointment.id), "window_start": window_start.isoformat(),
                                   "window_end": window_end.isoformat()},
                          actor=actor or "system", correlation_id=correlation_id or work_order.correlation_id)
    # The appointment now needs customer confirmation.
    appointment_transition(appointment.status, "CUSTOMER_CONFIRMATION_PENDING")
    appointment.status = "CUSTOMER_CONFIRMATION_PENDING"
    outbox(session, "workforce.work_order.scheduled.v1", tenant_id, correlation_id or work_order.correlation_id,
           {"work_order_id": str(work_order.id), "work_order_number": work_order.work_order_number,
            "appointment_id": str(appointment.id)})
    outbox(session, "workforce.appointment.confirmation_requested.v1", tenant_id,
           correlation_id or work_order.correlation_id,
           {"work_order_id": str(work_order.id), "appointment_id": str(appointment.id),
            "window_start": window_start.isoformat(), "window_end": window_end.isoformat()})
    return appointment


def confirm(session: Session, tenant_id, appointment_id: uuid.UUID, *, actor: str | None = None,
            correlation_id: str | None = None) -> Appointment:
    appointment = get_appointment_or_404(session, tenant_id, appointment_id)
    appointment_transition(appointment.status, "CONFIRMED")
    appointment.status = "CONFIRMED"
    appointment.confirmed_at = _now()
    work_order = session.get(WorkOrder, appointment.work_order_id)
    append_event(session, work_order, "work_order.appointment_confirmed",
                 payload={"appointment_id": str(appointment.id)}, actor_type="system" if actor is None else "agent",
                 actor_id=actor, correlation_id=correlation_id or work_order.correlation_id)
    outbox(session, "workforce.work_order.appointment_confirmed.v1", tenant_id,
           correlation_id or work_order.correlation_id,
           {"work_order_id": str(work_order.id), "work_order_number": work_order.work_order_number,
            "appointment_id": str(appointment.id)})
    session.flush()
    return appointment


def reschedule(session: Session, tenant_id, appointment_id: uuid.UUID, *, window_start: datetime, window_end: datetime,
               reason: str, actor: str | None = None, correlation_id: str | None = None) -> Appointment:
    previous = get_appointment_or_404(session, tenant_id, appointment_id)
    appointment_transition(previous.status, "RESCHEDULED")
    previous.status = "RESCHEDULED"
    previous.cancellation_reason = reason or "rescheduled"
    work_order = session.get(WorkOrder, previous.work_order_id)
    new_appointment = schedule(session, tenant_id, work_order, window_start=window_start, window_end=window_end,
                               customer_preferred=False, actor=actor, correlation_id=correlation_id)
    append_event(session, work_order, "work_order.appointment_rescheduled",
                 payload={"from_appointment": str(previous.id), "to_appointment": str(new_appointment.id), "reason": reason},
                 actor_type="agent" if actor else "system", actor_id=actor,
                 correlation_id=correlation_id or work_order.correlation_id)
    outbox(session, "workforce.work_order.appointment_rescheduled.v1", tenant_id,
           correlation_id or work_order.correlation_id,
           {"work_order_id": str(work_order.id), "work_order_number": work_order.work_order_number,
            "appointment_id": str(new_appointment.id), "reason": reason})
    session.flush()
    return new_appointment


def send_reminder(session: Session, tenant_id, appointment_id: uuid.UUID, *, channel: str = "SMS") -> bool:
    """Send a reminder asynchronously (notification service)."""
    appointment = get_appointment_or_404(session, tenant_id, appointment_id)
    if appointment.status not in ("CUSTOMER_CONFIRMATION_PENDING", "CONFIRMED"):
        return False
    work_order = session.get(WorkOrder, appointment.work_order_id)
    get_adapter("notifications").send(
        channel=channel.lower(), recipient=f"customer:{work_order.customer_id}", template="appointment_reminder",
        variables={"work_order_number": work_order.work_order_number,
                   "window_start": appointment.window_start.isoformat()},
        correlation_id=appointment.correlation_id)
    appointment.reminder_sent_at = _now()
    session.flush()
    return True


def mark_no_show(session: Session, tenant_id, appointment_id: uuid.UUID, *, who: str = "CUSTOMER",
                 reason: str | None = None, actor: str | None = None) -> Appointment:
    appointment = get_appointment_or_404(session, tenant_id, appointment_id)
    target = "CUSTOMER_NO_SHOW" if who == "CUSTOMER" else "TECHNICIAN_NO_SHOW"
    appointment_transition(appointment.status, target)
    appointment.status = target
    appointment.cancellation_reason = reason or f"{who} no show"
    work_order = session.get(WorkOrder, appointment.work_order_id)
    append_event(session, work_order, "work_order.appointment_no_show",
                 payload={"appointment_id": str(appointment.id), "who": who, "reason": reason},
                 actor_type="agent" if actor else "system", actor_id=actor,
                 correlation_id=appointment.correlation_id)
    session.flush()
    return appointment
