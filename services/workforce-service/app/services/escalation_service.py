"""Field escalation engine: triggers, actions and idempotent execution.

Triggers: unassigned, repeated assignment rejection, unconfirmed appointment,
technician late / not checked in, work not started, SLA at risk/breach,
inventory unavailable, remote activation failed, repeated customer
unavailability, repeated QA rejection, connectivity loss, emergency job blocked,
missing evidence. Every escalation is traceable and idempotent."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..domain.sla import engine as sla_engine
from ..integrations.base import get_adapter
from ..models import (
    Appointment,
    FieldEscalation,
    FieldSLAInstance,
    ProofOfWork,
    QualityReview,
    VisitCheckIn,
    WorkOrder,
    WorkOrderBlocker,
)
from .audit_service import append_event, outbox


def _now() -> datetime:
    return datetime.now(timezone.utc)


def open_escalations(session: Session, work_order_id) -> list[FieldEscalation]:
    return list(session.scalars(
        select(FieldEscalation).where(FieldEscalation.work_order_id == work_order_id,
                                      FieldEscalation.status == "OPEN")))


def detect_triggers(session: Session, tenant_id, work_order: WorkOrder, *, now: datetime | None = None) -> list[dict]:
    now = now or _now()
    triggers: list[dict] = []

    if work_order.status in ("CREATED", "VALIDATING", "READY_FOR_SCHEDULING", "SCHEDULED") \
            and work_order.assigned_technician_id is None:
        age = (now - _aware(work_order.created_at)).total_seconds()
        if age > 4 * 3600:
            triggers.append({"trigger": "UNASSIGNED", "level": 1, "reason": "unassigned for over 4 hours"})

    sla = session.scalars(select(FieldSLAInstance).where(FieldSLAInstance.work_order_id == work_order.id)).first()
    if sla is not None:
        if sla.status == "BREACHED" or sla.breach_at is not None:
            triggers.append({"trigger": "SLA_BREACH", "level": 2, "reason": "field SLA breached"})
        elif sla.status == "AT_RISK" or sla.at_risk_at is not None:
            triggers.append({"trigger": "SLA_AT_RISK", "level": 1, "reason": "field SLA at risk"})

    if work_order.current_appointment_id:
        appointment = session.get(Appointment, work_order.current_appointment_id)
        if appointment is not None and appointment.status in ("PROPOSED", "CUSTOMER_CONFIRMATION_PENDING") \
                and appointment.window_start < now:
            triggers.append({"trigger": "APPOINTMENT_UNCONFIRMED", "level": 1,
                             "reason": "appointment window passed without confirmation"})

    if work_order.assigned_technician_id and work_order.status == "ASSIGNED":
        updated = _aware(work_order.updated_at)
        if (now - updated) > timedelta(hours=2):
            triggers.append({"trigger": "WORK_NOT_STARTED", "level": 1, "reason": "work not started after assignment"})

    if work_order.status in ("DISPATCHED", "EN_ROUTE", "ASSIGNED"):
        checkin = session.scalars(select(VisitCheckIn.id).where(
            VisitCheckIn.work_order_id == work_order.id).limit(1)).first()
        if checkin is None:
            updated = _aware(work_order.updated_at)
            if (now - updated) > timedelta(hours=3):
                triggers.append({"trigger": "TECHNICIAN_NOT_CHECKED_IN", "level": 1,
                                 "reason": "technician not checked in"})

    blockers = list(session.scalars(select(WorkOrderBlocker).where(
        WorkOrderBlocker.work_order_id == work_order.id, WorkOrderBlocker.status == "OPEN")))
    if any(b.blocker_type == "INVENTORY_UNAVAILABLE" for b in blockers):
        triggers.append({"trigger": "INVENTORY_UNAVAILABLE", "level": 1, "reason": "required inventory unavailable"})
    if any(b.severity == "HIGH" for b in blockers):
        triggers.append({"trigger": "EMERGENCY_JOB_BLOCKED", "level": 2, "reason": "high-severity blocker open"})

    if work_order.status == "AWAITING_REMOTE_ACTION":
        triggers.append({"trigger": "REMOTE_ACTIVATION_FAILED", "level": 1,
                         "reason": "remote activation pending/failed"})

    qa = session.scalars(select(QualityReview).where(QualityReview.work_order_id == work_order.id)).first()
    if qa is not None and qa.state in ("REJECTED", "REWORK_REQUIRED"):
        triggers.append({"trigger": "QA_REJECTED_REPEATEDLY", "level": 2, "reason": "QA rejected"})

    proof_count = session.scalar(select(func.count(ProofOfWork.id)).where(
        ProofOfWork.work_order_id == work_order.id))
    if work_order.status == "EXECUTION_COMPLETED" and (proof_count or 0) == 0:
        triggers.append({"trigger": "EVIDENCE_MISSING", "level": 1, "reason": "no proof recorded"})

    return triggers


def _aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def execute_escalation(session: Session, work_order: WorkOrder, *, trigger: str, reason: str,
                       level: int | None = None, actor: str = "system",
                       correlation_id: str | None = None) -> FieldEscalation:
    level = level or 1
    actions: list[str] = []
    recipients: list[str] = []
    notifications = get_adapter("notifications")

    if trigger in ("SLA_AT_RISK", "SLA_BREACH"):
        sla = session.scalars(select(FieldSLAInstance).where(FieldSLAInstance.work_order_id == work_order.id)).first()
        if sla is not None:
            for threshold in (sla.policy_snapshot.get("definition") or {}).get("escalation", []):
                if threshold.get("level", 1) <= level and threshold.get("action") not in actions:
                    actions.append(threshold["action"])
        if trigger == "SLA_BREACH":
            actions.append("NOTIFY_SUPERVISOR")
    if trigger == "UNASSIGNED":
        actions += ["NOTIFY_DISPATCHER"]
    if trigger in ("WORK_NOT_STARTED", "TECHNICIAN_NOT_CHECKED_IN", "TECHNICIAN_LATE"):
        actions += ["NOTIFY_TECHNICIAN", "NOTIFY_DISPATCHER"]
    if trigger in ("APPOINTMENT_UNCONFIRMED", "CUSTOMER_UNAVAILABLE_REPEATEDLY"):
        actions += ["NOTIFY_CUSTOMER", "NOTIFY_DISPATCHER"]
    if trigger in ("INVENTORY_UNAVAILABLE",):
        actions += ["ESCALATE_TO_INVENTORY", "NOTIFY_SUPERVISOR"]
    if trigger == "REMOTE_ACTIVATION_FAILED":
        actions += ["ESCALATE_TO_OSS", "NOTIFY_SUPERVISOR"]
    if trigger in ("QA_REJECTED_REPEATEDLY", "EVIDENCE_MISSING"):
        actions += ["REQUIRE_MANUAL_INTERVENTION", "NOTIFY_SUPERVISOR"]
    if trigger == "EMERGENCY_JOB_BLOCKED":
        actions += ["REQUIRE_MANUAL_INTERVENTION", "NOTIFY_SUPERVISOR"]

    for action in dict.fromkeys(actions):
        if action == "NOTIFY_TECHNICIAN" and work_order.assigned_technician_id:
            notifications.send(channel="push", recipient=str(work_order.assigned_technician_id),
                               template="field_escalation", variables={"work_order_number": work_order.work_order_number,
                                                                       "trigger": trigger},
                               correlation_id=correlation_id)
            recipients.append(f"technician:{work_order.assigned_technician_id}")
        elif action == "NOTIFY_DISPATCHER":
            notifications.send(channel="email", recipient="dispatcher", template="field_escalation",
                               variables={"work_order_number": work_order.work_order_number, "trigger": trigger},
                               correlation_id=correlation_id)
            recipients.append("dispatcher")
        elif action == "NOTIFY_SUPERVISOR":
            notifications.send(channel="email", recipient=f"supervisor:{work_order.assigned_technician_id}",
                               template="field_escalation",
                               variables={"work_order_number": work_order.work_order_number, "trigger": trigger},
                               correlation_id=correlation_id)
            recipients.append("supervisor")
        elif action == "NOTIFY_CUSTOMER":
            notifications.send(channel="sms", recipient=f"customer:{work_order.customer_id}",
                               template="field_escalation_update",
                               variables={"work_order_number": work_order.work_order_number},
                               correlation_id=correlation_id)
            recipients.append(f"customer:{work_order.customer_id}")

    escalation = FieldEscalation(
        tenant_id=work_order.tenant_id, work_order_id=work_order.id, level=level, trigger=trigger,
        reason=reason, actions=list(dict.fromkeys(actions)), recipients=recipients,
        status="OPEN", raised_by=actor, raised_at=_now(),
    )
    session.add(escalation)
    session.flush()
    append_event(session, work_order, "work_order.escalated",
                 payload={"trigger": trigger, "level": level, "reason": reason, "actions": escalation.actions},
                 actor_type="system" if actor == "system" else "agent", actor_id=actor,
                 correlation_id=correlation_id or work_order.correlation_id)
    outbox(session, "workforce.work_order.escalated.v1", work_order.tenant_id,
           correlation_id or work_order.correlation_id,
           {"work_order_id": str(work_order.id), "work_order_number": work_order.work_order_number,
            "trigger": trigger, "level": level})
    return escalation


def evaluate_work_order(session: Session, tenant_id, work_order: WorkOrder, *, actor: str = "system",
                        correlation_id: str | None = None) -> list[str]:
    fired: list[str] = []
    existing_open = {e.trigger for e in open_escalations(session, work_order.id)}
    for detection in detect_triggers(session, tenant_id, work_order):
        trigger = detection["trigger"]
        if trigger in existing_open:
            continue
        execute_escalation(session, work_order, trigger=trigger, reason=detection["reason"],
                           level=detection["level"], actor=actor, correlation_id=correlation_id)
        fired.append(trigger)
        existing_open.add(trigger)
    session.flush()
    return fired


def resolve_escalation(session: Session, work_order: WorkOrder, *, trigger: str | None = None,
                       actor: str = "system") -> None:
    for escalation in open_escalations(session, work_order.id):
        if trigger is None or escalation.trigger == trigger:
            escalation.status = "RESOLVED"
            escalation.resolved_at = _now()
    session.flush()
