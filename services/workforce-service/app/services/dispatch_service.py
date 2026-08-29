"""Dispatch service: unassigned work, technician availability/capacity,
assignment recommendations, dispatch-board data, route sequences, conflict
validation and bulk assignment preview with optimistic concurrency."""
from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain import dispatch as dispatch_rules
from ..domain import technicians as tech_rules
from ..domain.assignment import select_technician
from ..domain.exceptions import NotFoundError, ScheduleConflictError, ValidationError
from ..models import Appointment, DispatchPlan, TechnicianProfile, WorkOrder
from . import catalog_service, technician_service, workorder_service
from .audit_service import correlation


def _now() -> datetime:
    return datetime.now(timezone.utc)


def unassigned_work(session: Session, tenant_id, *, limit: int = 200) -> list[WorkOrder]:
    return list(session.scalars(
        select(WorkOrder).where(
            WorkOrder.tenant_id == tenant_id,
            WorkOrder.assigned_technician_id.is_(None),
            WorkOrder.status.in_(("CREATED", "VALIDATING", "READY_FOR_SCHEDULING", "SCHEDULED", "RESCHEDULE_REQUIRED")),
        ).order_by(WorkOrder.priority, WorkOrder.created_at).limit(limit)))


def technician_board(session: Session, tenant_id, on_date: date) -> list[dict]:
    technicians = list(session.scalars(select(TechnicianProfile).where(
        TechnicianProfile.tenant_id == tenant_id, TechnicianProfile.is_active.is_(True))))
    board = []
    for tech in technicians:
        workload = dispatch_rules._daily_workload_seconds(session, tenant_id, tech.id, on_date)
        capacity = tech.max_daily_capacity * 3600
        board.append({
            "technician_id": str(tech.id),
            "name": tech.name,
            "status": tech.operational_status,
            "workload_minutes": round(workload / 60),
            "capacity_minutes": tech.max_daily_capacity * 60,
            "capacity_pct": round(100 * workload / capacity, 1) if capacity else 0,
        })
    return board


def recommendations(session: Session, tenant_id, work_order_id: uuid.UUID, *, limit: int = 5) -> list[dict]:
    """Assignment recommendations with persisted score breakdowns."""
    work_order = workorder_service.get_work_order_or_404(session, tenant_id, work_order_id)
    definition = work_order.template_snapshot or {}
    candidates = list(session.scalars(
        select(TechnicianProfile).where(
            TechnicianProfile.tenant_id == tenant_id,
            TechnicianProfile.is_active.is_(True),
            TechnicianProfile.operational_status.notin_(("OFF_SHIFT", "UNAVAILABLE", "EMERGENCY_UNAVAILABLE")),
        ).limit(50)))
    scored = []
    for technician in candidates:
        from ..domain.assignment import score_technician

        score, breakdown = score_technician(
            session, tenant_id, work_order, technician,
            required_skills=definition.get("required_skills"),
            required_certifications=definition.get("required_certifications"))
        scored.append({"technician_id": str(technician.id), "name": technician.name, "score": round(score, 1),
                       "breakdown": breakdown, "status": technician.operational_status})
    scored.sort(key=lambda r: -r["score"])
    return scored[:limit]


def validate_assignment(session: Session, tenant_id, work_order_id: uuid.UUID, technician_id: uuid.UUID,
                        *, window_start: datetime | None = None, window_end: datetime | None = None) -> dict:
    """Validate a proposed assignment (skills, certs, availability, conflicts)."""
    work_order = workorder_service.get_work_order_or_404(session, tenant_id, work_order_id)
    technician = technician_service.get_technician_or_404(session, tenant_id, technician_id)
    definition = work_order.template_snapshot or {}
    eligible, missing, reasons = tech_rules.meets_requirements(
        session, tenant_id, technician_id,
        required_skills=definition.get("required_skills"),
        required_certifications=definition.get("required_certifications"),
        work_order_type=work_order.work_order_type,
        service_area_id=work_order.service_area_id,
        on_date=(window_start or _now()).date(),
        start_time=(window_start or _now()).time(),
        end_time=(window_end or _now()).time())
    result = {"eligible": eligible, "missing": missing, "reasons": reasons}
    if window_start and window_end and eligible:
        try:
            dispatch_rules.validate_no_conflict(session, tenant_id, technician_id, window_start, window_end,
                                                work_order_id=work_order.id)
            result["conflicts"] = []
        except ScheduleConflictError as error:
            result["eligible"] = False
            result["conflicts"] = [str(error)]
    return result


def bulk_assignment_preview(session: Session, tenant_id, work_order_ids: list[uuid.UUID]) -> list[dict]:
    previews = []
    for work_order_id in work_order_ids:
        work_order = workorder_service.get_work_order_or_404(session, tenant_id, work_order_id)
        definition = work_order.template_snapshot or {}
        technician, score, breakdown = select_technician(
            session, tenant_id, work_order, strategy="SKILL_BASED",
            required_skills=definition.get("required_skills"),
            required_certifications=definition.get("required_certifications"))
        previews.append({
            "work_order_id": str(work_order.id),
            "work_order_number": work_order.work_order_number,
            "suggested_technician_id": str(technician.id) if technician else None,
            "suggested_technician_name": technician.name if technician else None,
            "score": score, "breakdown": breakdown,
        })
    return previews


def get_dispatch_plan(session: Session, tenant_id, technician_id: uuid.UUID, plan_date: str) -> DispatchPlan:
    plan = session.scalars(select(DispatchPlan).where(
        DispatchPlan.tenant_id == tenant_id, DispatchPlan.technician_id == technician_id,
        DispatchPlan.plan_date == plan_date)).first()
    if plan is None:
        plan = DispatchPlan(tenant_id=tenant_id, technician_id=technician_id, plan_date=plan_date,
                            sequence=[], version=1)
        session.add(plan)
        session.flush()
    return plan


def build_route(session: Session, tenant_id, technician_id: uuid.UUID, plan_date: str) -> dict:
    technician = technician_service.get_technician_or_404(session, tenant_id, technician_id)
    day_start = datetime.combine(date.fromisoformat(plan_date), time(0, 0), tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)
    orders = list(session.scalars(
        select(WorkOrder).where(WorkOrder.tenant_id == tenant_id,
                                WorkOrder.assigned_technician_id == technician_id,
                                WorkOrder.scheduled_start >= day_start,
                                WorkOrder.scheduled_start < day_end,
                                WorkOrder.status.notin_(("COMPLETED", "FAILED", "CANCELLED")))))
    return {"sequence": dispatch_rules.build_route_sequence(session, tenant_id, technician, orders)}
