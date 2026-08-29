"""Work-order command service: creation, validated lifecycle commands, SLA
effects, immutable events, outbox publication, assignment, and related-record
linking.

No code sets work_order.status directly; every command goes through the state
machine (flow.transition_work_order). Domain rules live here, never in views,
serializers or tasks."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain import numbering
from ..domain.assignment import select_technician
from ..domain.exceptions import NotFoundError, ValidationError
from ..enums import PRIORITIES, SEVERITIES, WORK_ORDER_RESULT_CODES, WORK_ORDER_SOURCE_CHANNELS, WORK_ORDER_TYPES
from ..models import (
    MaterialRequirement,
    TechnicianProfile,
    WorkOrder,
    WorkOrderAssignment,
    WorkOrderRelationship,
    WorkOrderResult,
)
from .audit_service import append_event, correlation, outbox
from .flow import transition_work_order


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_work_order_or_404(session: Session, tenant_id, work_order_id: uuid.UUID) -> WorkOrder:
    work_order = session.get(WorkOrder, work_order_id)
    if work_order is None or work_order.tenant_id != tenant_id:
        raise NotFoundError("work order not found")
    return work_order


def get_work_order_by_number(session: Session, tenant_id, number: str) -> WorkOrder:
    work_order = session.scalars(
        select(WorkOrder).where(WorkOrder.tenant_id == tenant_id, WorkOrder.work_order_number == number)).first()
    if work_order is None:
        raise NotFoundError("work order not found")
    return work_order


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------
def create_work_order(
    session: Session,
    tenant_id,
    *,
    work_order_type: str,
    customer_id: str | None = None,
    customer_name: str | None = None,
    service_subscription_id: str | None = None,
    service_location_id: str | None = None,
    oss_order_id: str | None = None,
    oss_order_number: str | None = None,
    support_ticket_id: str | None = None,
    support_ticket_number: str | None = None,
    nms_incident_id: str | None = None,
    billing_ref: str | None = None,
    franchise_id: str | None = None,
    reseller_id: str | None = None,
    branch_id: str | None = None,
    service_area_id: uuid.UUID | None = None,
    priority: str = "P3_MEDIUM",
    severity: str = "SEV3",
    latitude: float | None = None,
    longitude: float | None = None,
    address_line: str | None = None,
    scheduled_start: datetime | None = None,
    scheduled_end: datetime | None = None,
    instructions: str | None = None,
    source_channel: str = "API",
    strategy: str | None = None,
    correlation_id: str | None = None,
    idempotency_key: str | None = None,
    actor: str = "system",
    actor_type: str = "system",
) -> WorkOrder:
    from . import catalog_service, sla_service

    request_id = correlation(correlation_id)
    if idempotency_key:
        existing = session.scalars(
            select(WorkOrder).where(WorkOrder.tenant_id == tenant_id,
                                    WorkOrder.idempotency_key == idempotency_key)).first()
        if existing is not None:
            return existing

    work_order_type = work_order_type.upper()
    if work_order_type not in WORK_ORDER_TYPES:
        raise ValidationError(f"invalid work-order type {work_order_type!r}")
    priority = priority.upper()
    if priority not in PRIORITIES:
        raise ValidationError(f"invalid priority {priority!r}")
    severity = severity.upper()
    if severity not in SEVERITIES:
        raise ValidationError(f"invalid severity {severity!r}")
    if source_channel.upper() not in WORK_ORDER_SOURCE_CHANNELS:
        raise ValidationError(f"invalid source channel {source_channel!r}")

    template_version, definition = catalog_service.resolve_template(session, tenant_id, work_order_type)
    checklist_version, checklist_items = catalog_service.resolve_checklist(
        session, definition.get("checklist_template_id"), work_order_type)
    checklist_snapshot = {
        "template_id": str(checklist_version.template_id) if checklist_version else None,
        "template_version": checklist_version.version if checklist_version else 1,
        "items": [{"code": i.code, "label": i.label, "item_type": i.item_type, "required": i.required,
                   "rule": i.rule, "constraints": i.constraints} for i in checklist_items],
    }

    number = numbering.next_work_order_number(session, tenant_id)
    work_order = WorkOrder(
        tenant_id=tenant_id,
        work_order_number=number,
        work_order_type=work_order_type,
        template_version=template_version.version,
        priority=priority,
        severity=severity,
        status="CREATED",
        dispatch_state="UNASSIGNED",
        source_channel=source_channel.upper(),
        customer_id=customer_id,
        customer_name=customer_name,
        service_subscription_id=service_subscription_id,
        service_location_id=service_location_id,
        oss_order_id=oss_order_id,
        oss_order_number=oss_order_number,
        support_ticket_id=support_ticket_id,
        support_ticket_number=support_ticket_number,
        nms_incident_id=nms_incident_id,
        billing_ref=billing_ref,
        franchise_id=franchise_id,
        reseller_id=reseller_id,
        branch_id=branch_id,
        service_area_id=service_area_id,
        latitude=latitude,
        longitude=longitude,
        address_line=address_line,
        scheduled_start=scheduled_start,
        scheduled_end=scheduled_end,
        expected_duration_minutes=int(definition.get("expected_duration_minutes", 60)),
        instructions=instructions,
        completion_requirements=definition.get("completion_rules", {}),
        template_snapshot=definition,
        checklist_snapshot=checklist_snapshot,
        created_by=actor,
        correlation_id=request_id,
        idempotency_key=idempotency_key,
    )
    session.add(work_order)
    session.flush()

    # Material requirements from the template.
    for material in definition.get("required_consumables", []):
        session.add(MaterialRequirement(tenant_id=tenant_id, work_order_id=work_order.id,
                                        material_code=material, quantity=1, unit="UNIT", status="REQUIRED"))

    append_event(session, work_order, "work_order.created",
                 payload={"work_order_number": number, "work_order_type": work_order_type,
                          "priority": priority, "source_channel": source_channel.upper()},
                 actor_type=actor_type, actor_id=actor, correlation_id=request_id)
    outbox(session, "workforce.work_order.created.v1", tenant_id, request_id,
           {"work_order_id": str(work_order.id), "work_order_number": number,
            "work_order_type": work_order_type, "priority": priority})

    # Field SLA (fail-open; a missing default is a real config error surfaced in logs).
    try:
        policy, reason = sla_service.select_policy(session, tenant_id, work_order_type=work_order_type, priority=priority)
        version = sla_service.active_version(session, policy)
        calendar = catalog_service.get_or_create_calendar(session, tenant_id)
        sla = sla_service.instantiate_field_sla(session, work_order, policy, version, calendar,
                                                selected_reason=reason, priority=priority)
        work_order.field_sla_policy_id = policy.id
        work_order.field_sla_version = version.version
        work_order.field_sla_status = sla.status
        work_order.arrival_deadline = sla.arrival_deadline
        work_order.completion_deadline = sla.completion_deadline
    except Exception:  # noqa: BLE001 — never block creation on SLA config
        pass

    # Optional auto-assignment (best-effort; manual assignment is always possible).
    if strategy:
        try:
            assign_work_order(session, tenant_id, work_order.id, strategy=strategy, actor=actor,
                              correlation_id=request_id, auto=True)
        except Exception:  # noqa: BLE001
            pass

    session.flush()
    return work_order


# ---------------------------------------------------------------------------
# Validate / assign / dispatch
# ---------------------------------------------------------------------------
def validate_work_order(session: Session, tenant_id, work_order_id: uuid.UUID, *, actor: str = "system",
                        correlation_id: str | None = None) -> WorkOrder:
    work_order = get_work_order_or_404(session, tenant_id, work_order_id)
    work_order = transition_work_order(session, tenant_id, work_order, "VALIDATING", event_type="work_order.validated",
                                       actor=actor, correlation_id=correlation_id or work_order.correlation_id)
    # Validation completes into the scheduling queue (never leaves a half step).
    work_order = transition_work_order(session, tenant_id, work_order, "READY_FOR_SCHEDULING",
                                       event_type="work_order.ready_for_scheduling",
                                       actor=actor, correlation_id=correlation_id or work_order.correlation_id)
    outbox(session, "workforce.work_order.validated.v1", tenant_id, correlation_id or work_order.correlation_id,
           {"work_order_id": str(work_order.id), "work_order_number": work_order.work_order_number})
    session.flush()
    return work_order


def assign_work_order(session: Session, tenant_id, work_order_id: uuid.UUID, *, strategy: str = "SKILL_BASED",
                      technician_id: uuid.UUID | None = None, reason: str | None = None,
                      actor: str = "system", correlation_id: str | None = None, auto: bool = False) -> WorkOrder:
    from ..enums import ASSIGNMENT_STRATEGIES

    strategy = strategy.upper()
    if strategy not in ASSIGNMENT_STRATEGIES:
        raise ValidationError(f"invalid assignment strategy {strategy!r}")
    work_order = get_work_order_or_404(session, tenant_id, work_order_id)
    if work_order.status in ("COMPLETED", "FAILED", "CANCELLED"):
        raise ValidationError(f"cannot assign a {work_order.status} work order")

    definition = work_order.template_snapshot or {}
    required_skills = definition.get("required_skills", [])
    required_certifications = definition.get("required_certifications", [])

    selected = None
    score = None
    breakdown: dict = {}
    if technician_id is not None:
        # Manual override with reason.
        if not reason:
            raise ValidationError("manual assignment requires a reason")
        selected = session.get(TechnicianProfile, technician_id)
        if selected is None or selected.tenant_id != tenant_id:
            raise NotFoundError("technician not found")
        score, breakdown = _score_one(session, tenant_id, work_order, selected, required_skills, required_certifications)
    else:
        selected, score, breakdown = select_technician(
            session, tenant_id, work_order, strategy=strategy,
            required_skills=required_skills, required_certifications=required_certifications)
        if selected is None:
            # No qualified candidate; leave unassigned and let the unassigned queue flag it.
            return work_order

    work_order.assigned_technician_id = selected.id
    work_order.assigned_technician_name = selected.name
    work_order.dispatch_state = "ASSIGNED"
    session.add(WorkOrderAssignment(
        tenant_id=tenant_id, work_order_id=work_order.id, technician_id=selected.id, team_code=selected.team_code,
        strategy=strategy, reason=reason or f"auto:{strategy}", score=score, score_breakdown=breakdown,
        status="ACTIVE", assigned_by=actor,
    ))
    from . import technician_service

    technician_service.transition_status(session, tenant_id, selected.id, to_status="RESERVED",
                                         work_order_id=work_order.id, source="SYSTEM" if auto else "API",
                                         actor=actor, correlation_id=correlation_id or work_order.correlation_id)
    reassigning = work_order.status == "ASSIGNED"
    event_type = "work_order.reassigned" if reassigning else "work_order.assigned"
    if not reassigning:
        work_order = transition_work_order(session, tenant_id, work_order, "ASSIGNED", event_type=event_type,
                                           payload={"technician_id": str(selected.id), "strategy": strategy,
                                                    "score": score, "reason": reason},
                                           actor=actor, correlation_id=correlation_id or work_order.correlation_id)
    else:
        append_event(session, work_order, event_type,
                     payload={"from": "ASSIGNED", "to": "ASSIGNED", "technician_id": str(selected.id),
                              "strategy": strategy, "score": score, "reason": reason},
                     actor_type="agent", actor_id=actor,
                     correlation_id=correlation_id or work_order.correlation_id)
    outbox(session, "workforce.work_order.assigned.v1", tenant_id, correlation_id or work_order.correlation_id,
           {"work_order_id": str(work_order.id), "work_order_number": work_order.work_order_number,
            "technician_id": str(selected.id), "strategy": strategy})
    session.flush()
    return work_order


def _score_one(session, tenant_id, work_order, technician, required_skills, required_certifications):
    from ..domain.assignment import score_technician

    return score_technician(session, tenant_id, work_order, technician,
                            required_skills=required_skills, required_certifications=required_certifications)


def accept_assignment(session: Session, tenant_id, work_order_id: uuid.UUID, *, technician_id: uuid.UUID,
                      actor: str = "system", correlation_id: str | None = None) -> WorkOrder:
    work_order = get_work_order_or_404(session, tenant_id, work_order_id)
    if str(work_order.assigned_technician_id) != str(technician_id):
        raise ValidationError("assignment not for this technician")
    if work_order.status not in ("ASSIGNED", "DISPATCHED", "EN_ROUTE", "ARRIVED"):
        raise ValidationError(f"cannot accept assignment from state {work_order.status}")
    # Acceptance is an acknowledgment; it never rewinds an already-dispatched order.
    append_event(session, work_order, "work_order.assignment_accepted",
                 payload={"technician_id": str(technician_id)},
                 actor_type="technician", actor_id=actor,
                 correlation_id=correlation_id or work_order.correlation_id)
    outbox(session, "workforce.work_order.assignment_accepted.v1", tenant_id,
           correlation_id or work_order.correlation_id,
           {"work_order_id": str(work_order.id), "work_order_number": work_order.work_order_number})
    session.flush()
    return work_order


def reject_assignment(session: Session, tenant_id, work_order_id: uuid.UUID, *, technician_id: uuid.UUID,
                      reason: str, actor: str = "system", correlation_id: str | None = None) -> WorkOrder:
    work_order = get_work_order_or_404(session, tenant_id, work_order_id)
    if str(work_order.assigned_technician_id) != str(technician_id):
        raise ValidationError("assignment not for this technician")
    from . import technician_service

    technician_service.transition_status(session, tenant_id, technician_id, to_status="AVAILABLE",
                                         work_order_id=work_order.id, source="MOBILE", actor=actor,
                                         correlation_id=correlation_id)
    work_order.assigned_technician_id = None
    work_order.assigned_technician_name = None
    work_order.dispatch_state = "UNASSIGNED"
    work_order = transition_work_order(session, tenant_id, work_order, "READY_FOR_SCHEDULING",
                                       event_type="work_order.assignment_rejected",
                                       payload={"reason": reason, "technician_id": str(technician_id)},
                                       actor=actor, correlation_id=correlation_id or work_order.correlation_id)
    session.flush()
    return work_order


def dispatch_work_order(session: Session, tenant_id, work_order_id: uuid.UUID, *, actor: str = "system",
                        correlation_id: str | None = None) -> WorkOrder:
    work_order = get_work_order_or_404(session, tenant_id, work_order_id)
    if work_order.assigned_technician_id is None:
        raise ValidationError("cannot dispatch an unassigned work order")
    from . import technician_service

    technician_service.transition_status(session, tenant_id, work_order.assigned_technician_id, to_status="DISPATCHED",
                                         work_order_id=work_order.id, source="API", actor=actor,
                                         correlation_id=correlation_id)
    # Update the appointment state.
    if work_order.current_appointment_id:
        _advance_appointment(session, tenant_id, work_order.current_appointment_id, "TECHNICIAN_DISPATCHED")
    work_order.dispatch_state = "DISPATCHED"
    work_order = transition_work_order(session, tenant_id, work_order, "DISPATCHED", event_type="work_order.dispatched",
                                       actor=actor, correlation_id=correlation_id or work_order.correlation_id)
    outbox(session, "workforce.work_order.dispatched.v1", tenant_id, correlation_id or work_order.correlation_id,
           {"work_order_id": str(work_order.id), "work_order_number": work_order.work_order_number,
            "technician_id": str(work_order.assigned_technician_id)})
    session.flush()
    return work_order


def _advance_appointment(session, tenant_id, appointment_id, target: str):
    from ..models import Appointment
    from ..state_machine import appointment_transition

    appointment = session.get(Appointment, appointment_id)
    if appointment is None:
        return
    try:
        appointment_transition(appointment.status, target)
        appointment.status = target
    except Exception:  # noqa: BLE001 — appointment state is best-effort relative to the work order
        pass


def start_travel(session: Session, tenant_id, work_order_id: uuid.UUID, *, actor: str = "system",
                 correlation_id: str | None = None) -> WorkOrder:
    work_order = get_work_order_or_404(session, tenant_id, work_order_id)
    if work_order.assigned_technician_id:
        from . import technician_service

        technician_service.transition_status(session, tenant_id, work_order.assigned_technician_id, to_status="EN_ROUTE",
                                             work_order_id=work_order.id, source="MOBILE", actor=actor,
                                             correlation_id=correlation_id)
    work_order = transition_work_order(session, tenant_id, work_order, "EN_ROUTE", event_type="work_order.en_route",
                                       actor=actor, correlation_id=correlation_id or work_order.correlation_id)
    session.flush()
    return work_order


def start_work(session: Session, tenant_id, work_order_id: uuid.UUID, *, actor: str = "system",
               correlation_id: str | None = None) -> WorkOrder:
    work_order = get_work_order_or_404(session, tenant_id, work_order_id)
    from . import technician_service

    if work_order.assigned_technician_id:
        technician_service.transition_status(session, tenant_id, work_order.assigned_technician_id, to_status="WORKING",
                                             work_order_id=work_order.id, source="MOBILE", actor=actor,
                                             correlation_id=correlation_id)
    work_order = transition_work_order(session, tenant_id, work_order, "IN_PROGRESS", event_type="work_order.execution_started",
                                       actor=actor, correlation_id=correlation_id or work_order.correlation_id)
    outbox(session, "workforce.work_order.execution_started.v1", tenant_id,
           correlation_id or work_order.correlation_id,
           {"work_order_id": str(work_order.id), "work_order_number": work_order.work_order_number})
    session.flush()
    return work_order


def pause_work_order(session: Session, tenant_id, work_order_id: uuid.UUID, *, reason: str, actor: str = "system",
                     correlation_id: str | None = None) -> WorkOrder:
    work_order = get_work_order_or_404(session, tenant_id, work_order_id)
    work_order = transition_work_order(session, tenant_id, work_order, "PAUSED", event_type="work_order.paused",
                                       payload={"reason": reason}, actor=actor,
                                       correlation_id=correlation_id or work_order.correlation_id)
    session.flush()
    return work_order


def resume_work_order(session: Session, tenant_id, work_order_id: uuid.UUID, *, actor: str = "system",
                      correlation_id: str | None = None) -> WorkOrder:
    work_order = get_work_order_or_404(session, tenant_id, work_order_id)
    work_order = transition_work_order(session, tenant_id, work_order, "IN_PROGRESS", event_type="work_order.resumed",
                                       actor=actor, correlation_id=correlation_id or work_order.correlation_id)
    session.flush()
    return work_order


def record_blocker(session: Session, tenant_id, work_order_id: uuid.UUID, *, blocker_type: str, reason: str,
                   severity: str = "MEDIUM", actor: str = "system", correlation_id: str | None = None) -> WorkOrder:
    from ..models import WorkOrderBlocker

    work_order = get_work_order_or_404(session, tenant_id, work_order_id)
    session.add(WorkOrderBlocker(tenant_id=tenant_id, work_order_id=work_order.id, blocker_type=blocker_type,
                                 reason=reason, severity=severity, status="OPEN", raised_by=actor,
                                 correlation_id=correlation_id or work_order.correlation_id))
    work_order = transition_work_order(session, tenant_id, work_order, "BLOCKED", event_type="work_order.blocked",
                                       payload={"blocker_type": blocker_type, "reason": reason},
                                       actor=actor, correlation_id=correlation_id or work_order.correlation_id)
    outbox(session, "workforce.work_order.blocked.v1", tenant_id, correlation_id or work_order.correlation_id,
           {"work_order_id": str(work_order.id), "work_order_number": work_order.work_order_number,
            "blocker_type": blocker_type})
    session.flush()
    return work_order


def request_parts(session: Session, tenant_id, work_order_id: uuid.UUID, *, materials: list, reason: str | None = None,
                  actor: str = "system", correlation_id: str | None = None) -> WorkOrder:
    work_order = get_work_order_or_404(session, tenant_id, work_order_id)
    for material in materials:
        code = material.get("material_code")
        quantity = material.get("quantity", 1)
        if code:
            requirement = session.scalars(select(MaterialRequirement).where(
                MaterialRequirement.work_order_id == work_order.id,
                MaterialRequirement.material_code == code)).first()
            if requirement is None:
                requirement = MaterialRequirement(tenant_id=tenant_id, work_order_id=work_order.id,
                                                  material_code=code, quantity=quantity, unit="UNIT", status="REQUIRED")
                session.add(requirement)
            else:
                requirement.quantity = max(requirement.quantity, quantity)
    work_order = transition_work_order(session, tenant_id, work_order, "AWAITING_PARTS", event_type="work_order.parts_requested",
                                       payload={"materials": materials, "reason": reason}, actor=actor,
                                       correlation_id=correlation_id or work_order.correlation_id)
    session.flush()
    return work_order


def request_remote_action(session: Session, tenant_id, work_order_id: uuid.UUID, *, actor: str = "system",
                          correlation_id: str | None = None, oss_order_id: str | None = None) -> WorkOrder:
    """Request remote activation through the OSS adapter (never direct device
    access from the technician API)."""
    from ..integrations.base import get_adapter

    work_order = get_work_order_or_404(session, tenant_id, work_order_id)
    order_id = oss_order_id or work_order.oss_order_id or ""
    result = get_adapter("oss").request_remote_activation(
        order_id=order_id, work_order_id=str(work_order.id), actor=actor,
        correlation_id=correlation_id or work_order.correlation_id)
    work_order = transition_work_order(session, tenant_id, work_order, "AWAITING_REMOTE_ACTION",
                                       event_type="work_order.remote_action_requested",
                                       payload={"order_id": order_id, "result_ok": result.ok},
                                       actor=actor, correlation_id=correlation_id or work_order.correlation_id)
    outbox(session, "workforce.work_order.remote_action_requested.v1", tenant_id,
           correlation_id or work_order.correlation_id,
           {"work_order_id": str(work_order.id), "work_order_number": work_order.work_order_number,
            "order_id": order_id})
    session.flush()
    return work_order


# ---------------------------------------------------------------------------
# Execution completion / QA / complete
# ---------------------------------------------------------------------------
def finish_execution(session: Session, tenant_id, work_order_id: uuid.UUID, *, actor: str = "system",
                     correlation_id: str | None = None) -> WorkOrder:
    from . import checklist_service, proof_service

    work_order = get_work_order_or_404(session, tenant_id, work_order_id)
    # Checklist validation.
    checklist_ok, checklist_errors = checklist_service.checklist_is_complete(session, tenant_id, work_order)
    if not checklist_ok:
        from ..domain.exceptions import ChecklistError

        raise ChecklistError("checklist incomplete: " + "; ".join(checklist_errors))
    # Required proof validation.
    proof_missing = proof_service.required_proof_missing(session, tenant_id, work_order)
    if proof_missing:
        from ..domain.exceptions import ProofError

        raise ProofError("required proof missing: " + ", ".join(proof_missing))
    # Material reconciliation (requirements vs usage).
    mat_errors = proof_service.material_reconciliation_errors(session, tenant_id, work_order)
    if mat_errors:
        from ..domain.exceptions import ProofError

        raise ProofError("material reconciliation failed: " + "; ".join(mat_errors))

    work_order = transition_work_order(session, tenant_id, work_order, "EXECUTION_COMPLETED",
                                       event_type="work_order.execution_completed", actor=actor,
                                       correlation_id=correlation_id or work_order.correlation_id)
    outbox(session, "workforce.work_order.execution_completed.v1", tenant_id,
           correlation_id or work_order.correlation_id,
           {"work_order_id": str(work_order.id), "work_order_number": work_order.work_order_number})
    session.flush()
    return work_order


def submit_for_verification(session: Session, tenant_id, work_order_id: uuid.UUID, *, actor: str = "system",
                            correlation_id: str | None = None) -> WorkOrder:
    from . import qa_service

    work_order = get_work_order_or_404(session, tenant_id, work_order_id)
    work_order = transition_work_order(session, tenant_id, work_order, "VERIFICATION_PENDING",
                                       event_type="work_order.submitted_for_verification", actor=actor,
                                       correlation_id=correlation_id or work_order.correlation_id)
    qa_service.open_review(session, tenant_id, work_order)
    session.flush()
    return work_order


def complete_work_order(session: Session, tenant_id, work_order_id: uuid.UUID, *, result_code: str,
                        summary: str, root_cause_reference: str | None = None, actor: str = "system",
                        correlation_id: str | None = None) -> WorkOrder:
    from . import qa_service

    work_order = get_work_order_or_404(session, tenant_id, work_order_id)
    result_code = result_code.upper()
    if result_code not in WORK_ORDER_RESULT_CODES:
        raise ValidationError(f"invalid result code {result_code!r}")
    if not summary or not summary.strip():
        raise ValidationError("completion summary is required")
    qa = qa_service.get_review(session, work_order)
    if qa is not None and qa.state not in ("NOT_REQUIRED", "APPROVED"):
        raise ValidationError("work order cannot complete before QA approval")

    session.add(WorkOrderResult(tenant_id=tenant_id, work_order_id=work_order.id, result_code=result_code,
                                summary=summary.strip(), root_cause_reference=root_cause_reference,
                                recorded_by=actor))
    work_order.result_code = result_code
    work_order.result_summary = summary.strip()
    work_order = transition_work_order(session, tenant_id, work_order, "COMPLETED", event_type="work_order.completed",
                                       payload={"result_code": result_code}, actor=actor,
                                       correlation_id=correlation_id or work_order.correlation_id)
    if work_order.assigned_technician_id:
        from . import technician_service

        technician_service.transition_status(session, tenant_id, work_order.assigned_technician_id, to_status="AVAILABLE",
                                             work_order_id=work_order.id, source="SYSTEM", actor=actor,
                                             correlation_id=correlation_id)
    sla = _mark_field_sla_completed(session, work_order)
    outbox(session, "workforce.work_order.completed.v1", tenant_id, correlation_id or work_order.correlation_id,
           {"work_order_id": str(work_order.id), "work_order_number": work_order.work_order_number,
            "result_code": result_code})
    session.flush()
    return work_order


def _mark_field_sla_completed(session, work_order: WorkOrder):
    from . import sla_service

    sla = sla_service.get_field_sla(session, work_order)
    if sla is not None and sla.status != "BREACHED":
        sla.status = "COMPLETED"
        work_order.field_sla_status = sla.status
    return sla


def fail_work_order(session: Session, tenant_id, work_order_id: uuid.UUID, *, reason: str,
                    actor: str = "system", correlation_id: str | None = None) -> WorkOrder:
    work_order = get_work_order_or_404(session, tenant_id, work_order_id)
    if not reason or not reason.strip():
        raise ValidationError("failure requires a reason")
    work_order = transition_work_order(session, tenant_id, work_order, "FAILED", event_type="work_order.failed",
                                       payload={"reason": reason}, actor=actor,
                                       correlation_id=correlation_id or work_order.correlation_id)
    outbox(session, "workforce.work_order.failed.v1", tenant_id, correlation_id or work_order.correlation_id,
           {"work_order_id": str(work_order.id), "work_order_number": work_order.work_order_number, "reason": reason})
    session.flush()
    return work_order


def cancel_work_order(session: Session, tenant_id, work_order_id: uuid.UUID, *, reason: str,
                      actor: str = "system", correlation_id: str | None = None) -> WorkOrder:
    work_order = get_work_order_or_404(session, tenant_id, work_order_id)
    if work_order.status in ("COMPLETED", "FAILED", "CANCELLED"):
        raise ValidationError(f"cannot cancel a {work_order.status} work order")
    if not reason or not reason.strip():
        raise ValidationError("cancellation requires a reason")
    if work_order.assigned_technician_id:
        from . import technician_service

        technician_service.transition_status(session, tenant_id, work_order.assigned_technician_id, to_status="AVAILABLE",
                                             work_order_id=work_order.id, source="API", actor=actor,
                                             correlation_id=correlation_id)
    work_order = transition_work_order(session, tenant_id, work_order, "CANCELLED", event_type="work_order.cancelled",
                                       payload={"reason": reason}, actor=actor,
                                       correlation_id=correlation_id or work_order.correlation_id)
    outbox(session, "workforce.work_order.cancelled.v1", tenant_id, correlation_id or work_order.correlation_id,
           {"work_order_id": str(work_order.id), "work_order_number": work_order.work_order_number, "reason": reason})
    session.flush()
    return work_order


# ---------------------------------------------------------------------------
# Check-in / check-out (delegated to visit service)
# ---------------------------------------------------------------------------
def check_in_work_order(session: Session, tenant_id, work_order_id: uuid.UUID, *, technician_id: uuid.UUID,
                        payload: dict, actor: str = "system", correlation_id: str | None = None,
                        device_ref: str | None = None) -> WorkOrder:
    from . import visit_service

    visit, checkin = visit_service.perform_check_in(
        session, tenant_id, work_order_id, technician_id=technician_id, payload=payload,
        actor=actor, correlation_id=correlation_id or correlation(None), device_ref=device_ref)
    work_order = get_work_order_or_404(session, tenant_id, work_order_id)
    work_order = transition_work_order(session, tenant_id, work_order, "ARRIVED", event_type="work_order.technician_arrived",
                                       payload={"visit_id": str(visit.id)}, actor=actor,
                                       correlation_id=correlation_id or work_order.correlation_id)
    _advance_appointment(session, tenant_id, work_order.current_appointment_id, "TECHNICIAN_ARRIVED")
    outbox(session, "workforce.work_order.technician_arrived.v1", tenant_id,
           correlation_id or work_order.correlation_id,
           {"work_order_id": str(work_order.id), "work_order_number": work_order.work_order_number})
    session.flush()
    return work_order


def check_out_work_order(session: Session, tenant_id, work_order_id: uuid.UUID, *, technician_id: uuid.UUID,
                         payload: dict, actor: str = "system", correlation_id: str | None = None,
                         device_ref: str | None = None) -> WorkOrder:
    from . import visit_service

    visit, checkout = visit_service.perform_check_out(
        session, tenant_id, work_order_id, technician_id=technician_id, payload=payload,
        actor=actor, correlation_id=correlation_id or correlation(None), device_ref=device_ref)
    work_order = get_work_order_or_404(session, tenant_id, work_order_id)
    return work_order


# ---------------------------------------------------------------------------
# Links / relationships
# ---------------------------------------------------------------------------
def link_oss_order(session: Session, tenant_id, work_order_id: uuid.UUID, *, order_id: str,
                   order_number: str | None = None, actor: str = "system",
                   correlation_id: str | None = None) -> WorkOrder:
    work_order = get_work_order_or_404(session, tenant_id, work_order_id)
    work_order.oss_order_id = order_id
    work_order.oss_order_number = order_number
    append_event(session, work_order, "work_order.oss_order_linked",
                 payload={"order_id": order_id, "order_number": order_number}, actor_type="agent", actor_id=actor,
                 correlation_id=correlation_id or work_order.correlation_id)
    session.flush()
    return work_order


def link_ticket(session: Session, tenant_id, work_order_id: uuid.UUID, *, ticket_id: str,
                ticket_number: str | None = None, actor: str = "system",
                correlation_id: str | None = None) -> WorkOrder:
    work_order = get_work_order_or_404(session, tenant_id, work_order_id)
    work_order.support_ticket_id = ticket_id
    work_order.support_ticket_number = ticket_number
    append_event(session, work_order, "work_order.ticket_linked",
                 payload={"ticket_id": ticket_id, "ticket_number": ticket_number}, actor_type="agent", actor_id=actor,
                 correlation_id=correlation_id or work_order.correlation_id)
    session.flush()
    return work_order


def link_incident(session: Session, tenant_id, work_order_id: uuid.UUID, *, incident_id: str,
                  actor: str = "system", correlation_id: str | None = None) -> WorkOrder:
    work_order = get_work_order_or_404(session, tenant_id, work_order_id)
    work_order.nms_incident_id = incident_id
    append_event(session, work_order, "work_order.incident_linked",
                 payload={"incident_id": incident_id}, actor_type="agent", actor_id=actor,
                 correlation_id=correlation_id or work_order.correlation_id)
    session.flush()
    return work_order


def link_related(session: Session, tenant_id, work_order_id: uuid.UUID, *, relation_type: str,
                 to_work_order_id: uuid.UUID, actor: str = "system") -> WorkOrder:
    work_order = get_work_order_or_404(session, tenant_id, work_order_id)
    to_work_order = get_work_order_or_404(session, tenant_id, to_work_order_id)
    existing = session.scalars(select(WorkOrderRelationship).where(
        WorkOrderRelationship.from_work_order_id == work_order.id,
        WorkOrderRelationship.to_work_order_id == to_work_order.id,
        WorkOrderRelationship.relation_type == relation_type)).first()
    if existing is None:
        session.add(WorkOrderRelationship(tenant_id=tenant_id, from_work_order_id=work_order.id,
                                          to_work_order_id=to_work_order.id, relation_type=relation_type,
                                          created_by=actor))
        append_event(session, work_order, "work_order.relationship_linked",
                     payload={"relation_type": relation_type, "to": to_work_order.work_order_number},
                     actor_type="agent", actor_id=actor, correlation_id=work_order.correlation_id)
        session.flush()
    return work_order


def valid_actions(work_order: WorkOrder) -> list[str]:
    from ..state_machine import WORK_ORDER_TRANSITIONS

    return sorted(WORK_ORDER_TRANSITIONS[work_order.status])
