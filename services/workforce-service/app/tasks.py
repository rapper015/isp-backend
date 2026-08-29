"""Background maintenance tasks for the workforce service: field SLA
evaluation, escalations, appointment reminders, stuck/orphan detection,
certification expiry and outbox flush. All tasks are idempotent and
restart-safe."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .domain.sla import engine as sla_engine
from .models import (
    Appointment,
    OutboxEvent,
    TechnicianProfile,
    WorkOrder,
)
from .services import escalation_service, technician_service, workorder_service
from .services.sla_service import get_field_sla


def _now() -> datetime:
    return datetime.now(timezone.utc)


def flush_outbox(session: Session, limit: int = 100) -> list[str]:
    pending = list(session.scalars(
        select(OutboxEvent).where(OutboxEvent.published_at.is_(None)).order_by(OutboxEvent.occurred_at).limit(limit)))
    published: list[str] = []
    for event in pending:
        try:
            payload = {
                "event_type": event.event_type,
                "correlation_id": event.correlation_id,
                "idempotency_key": event.idempotency_key,
                "tenant_id": str(event.tenant_id) if event.tenant_id else None,
                "payload": event.payload,
                "occurred_at": event.occurred_at.isoformat(),
            }
            json.dumps(payload)  # serialization sanity; publish hook point
            event.published_at = datetime.now(timezone.utc)
            event.attempts += 1
            published.append(event.event_type)
        except Exception:  # noqa: BLE001
            event.attempts += 1
    session.commit()
    return published


def evaluate_field_slas(session: Session, limit: int = 200) -> dict:
    """Evaluate every active/paused/at-risk field SLA instance."""
    slas = _all_field_slas(session, limit)
    at_risk = 0
    breached = 0
    for sla in slas:
        result = sla_engine.evaluate_field_sla(session, sla, emit=True, consumer="field-sla-evaluator")
        if result["changed"]:
            session.flush()
            work_order = session.get(WorkOrder, sla.work_order_id)
            if work_order is not None:
                work_order.field_sla_status = sla.status
                try:
                    escalation_service.evaluate_work_order(session, work_order.tenant_id, work_order,
                                                           actor="field-sla-evaluator",
                                                           correlation_id=work_order.correlation_id)
                except Exception:  # noqa: BLE001
                    pass
            if result["breached"]:
                breached += 1
            elif result["at_risk"]:
                at_risk += 1
    session.commit()
    return {"evaluated": len(slas), "at_risk": at_risk, "breached": breached}


def _all_field_slas(session: Session, limit: int):
    from .models import FieldSLAInstance

    return list(session.scalars(select(FieldSLAInstance).where(
        FieldSLAInstance.status.in_(("ACTIVE", "AT_RISK", "PAUSED"))).limit(limit)))


def run_escalations(session: Session, limit: int = 200) -> list[str]:
    work_orders = list(session.scalars(
        select(WorkOrder).where(WorkOrder.status.notin_(("COMPLETED", "FAILED", "CANCELLED"))).limit(limit)))
    fired: list[str] = []
    for wo in work_orders:
        fired.extend(escalation_service.evaluate_work_order(
            session, wo.tenant_id, wo, actor="escalation-worker", correlation_id=wo.correlation_id))
    session.commit()
    return fired


def send_appointment_reminders(session: Session, *, minutes_before: int = 120, limit: int = 100) -> list[str]:
    """Send reminders for confirmed appointments starting soon."""
    window_start = _now() + timedelta(minutes=minutes_before)
    window_end = _now() + timedelta(minutes=minutes_before + 30)
    appointments = list(session.scalars(select(Appointment).where(
        Appointment.status.in_(("CUSTOMER_CONFIRMATION_PENDING", "CONFIRMED")),
        Appointment.reminder_sent_at.is_(None),
        Appointment.window_start >= window_start,
        Appointment.window_start <= window_end).limit(limit)))
    reminded = []
    from .services import appointment_service

    for appointment in appointments:
        try:
            appointment_service.send_reminder(session, appointment.tenant_id, appointment.id, channel="SMS")
            reminded.append(str(appointment.id))
        except Exception:  # noqa: BLE001
            pass
    session.commit()
    return reminded


def detect_stuck_work_orders(session: Session, hours: int = 72, limit: int = 200) -> list[dict]:
    cutoff = _now() - timedelta(hours=hours)
    orders = list(session.scalars(
        select(WorkOrder).where(WorkOrder.status.notin_(("COMPLETED", "FAILED", "CANCELLED"))).limit(limit)))
    stuck = []
    for wo in orders:
        updated = wo.updated_at if wo.updated_at.tzinfo else wo.updated_at.replace(tzinfo=timezone.utc)
        if updated < cutoff:
            stuck.append({"work_order_id": str(wo.id), "work_order_number": wo.work_order_number})
    return stuck


def detect_orphan_assignments(session: Session, tenant_id) -> list[dict]:
    """Work orders assigned to inactive technicians."""
    orphans = []
    orders = list(session.scalars(select(WorkOrder).where(
        WorkOrder.tenant_id == tenant_id, WorkOrder.assigned_technician_id.is_not(None))))
    active_ids = {t.id for t in session.scalars(select(TechnicianProfile).where(
        TechnicianProfile.tenant_id == tenant_id, TechnicianProfile.is_active.is_(True)))}
    for wo in orders:
        if wo.assigned_technician_id not in active_ids:
            orphans.append({"work_order_id": str(wo.id), "work_order_number": wo.work_order_number,
                            "technician_id": str(wo.assigned_technician_id)})
    return orphans


def expire_stale_certifications(session: Session, tenant_id) -> list[str]:
    return technician_service.expire_stale_certifications(session, tenant_id)
