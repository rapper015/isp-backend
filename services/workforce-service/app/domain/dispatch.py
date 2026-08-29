"""Dispatch-plan domain rules: route sequencing, conflict detection, capacity
warnings and optimistic concurrency.

A dispatch plan must never silently overwrite confirmed customer appointments.
Plan edits use an optimistic-concurrency version field; conflicting edits are
rejected."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.exceptions import NotFoundError, ScheduleConflictError
from ..models import Appointment, DispatchPlan, TechnicianProfile, WorkOrder
from .gps import get_maps_provider
from .technicians import open_work_count


def _now() -> datetime:
    return datetime.now(timezone.utc)


def appointment_overlaps(session: Session, tenant_id, technician_id, start: datetime, end: datetime,
                        exclude_work_order_id=None) -> list[Appointment]:
    """Confirmed/active appointments of a technician overlapping [start, end]."""
    rows = list(session.scalars(
        select(Appointment).join(WorkOrder, Appointment.work_order_id == WorkOrder.id).where(
            WorkOrder.tenant_id == tenant_id,
            WorkOrder.assigned_technician_id == technician_id,
            Appointment.status.notin_(("CANCELLED", "RESCHEDULED", "COMPLETED")),
            Appointment.window_start < end,
            Appointment.window_end > start,
        )))
    if exclude_work_order_id:
        rows = [r for r in rows if r.work_order_id != exclude_work_order_id]
    return rows


def validate_no_conflict(session: Session, tenant_id, technician_id, start: datetime, end: datetime,
                         work_order_id=None) -> None:
    overlaps = appointment_overlaps(session, tenant_id, technician_id, start, end, exclude_work_order_id=work_order_id)
    if overlaps:
        raise ScheduleConflictError(
            f"technician has {len(overlaps)} conflicting appointment(s) in this window")


def capacity_warning(session: Session, tenant_id, technician: TechnicianProfile, on_date) -> dict | None:
    workload_seconds = _daily_workload_seconds(session, tenant_id, technician.id, on_date)
    capacity_seconds = technician.max_daily_capacity * 60 * 60
    if workload_seconds >= capacity_seconds:
        return {"warning": "capacity_exceeded", "workload_minutes": round(workload_seconds / 60),
                "capacity_minutes": technician.max_daily_capacity * 60}
    if workload_seconds >= 0.8 * capacity_seconds:
        return {"warning": "capacity_high", "workload_minutes": round(workload_seconds / 60),
                "capacity_minutes": technician.max_daily_capacity * 60}
    return None


def _daily_workload_seconds(session: Session, tenant_id, technician_id, on_date) -> int:
    start = datetime.combine(on_date, datetime.min.time(), tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    rows = list(session.scalars(
        select(WorkOrder).where(
            WorkOrder.tenant_id == tenant_id,
            WorkOrder.assigned_technician_id == technician_id,
            WorkOrder.scheduled_start >= start,
            WorkOrder.scheduled_start < end,
            WorkOrder.status.notin_(("COMPLETED", "FAILED", "CANCELLED")))))
    return sum((r.expected_duration_minutes or 60) * 60 for r in rows)


def build_route_sequence(session: Session, tenant_id, technician: TechnicianProfile, work_orders: list[WorkOrder],
                         *, travel_buffer_minutes: int = 15) -> list[dict]:
    """Order work orders by proximity (nearest-neighbour) from the base location
    and return the sequence with travel buffers. Deterministic fallback."""
    current = (technician.base_lat, technician.base_lng)
    remaining = list(work_orders)
    sequence: list[dict] = []
    maps = get_maps_provider()
    while remaining:
        best = None
        best_distance = None
        for wo in remaining:
            if wo.latitude is None or wo.longitude is None:
                distance = 0.0
            else:
                distance = maps.travel_estimate(current[0], current[1], wo.latitude, wo.longitude)["distance_m"]
            if best_distance is None or distance < best_distance:
                best_distance = distance
                best = wo
        remaining.remove(best)
        estimate = maps.travel_estimate(current[0], current[1], best.latitude or current[0], best.longitude or current[1])
        sequence.append({
            "work_order_id": str(best.id),
            "work_order_number": best.work_order_number,
            "distance_m": round(estimate["distance_m"], 1),
            "travel_seconds": estimate["duration_s"],
            "travel_buffer_minutes": travel_buffer_minutes,
            "travel_source": estimate["source"],
        })
        if best.latitude is not None and best.longitude is not None:
            current = (best.latitude, best.longitude)
    return sequence


def apply_sequence(session: Session, tenant_id, plan_id, sequence: list, *, expected_version: int,
                   edited_by: str) -> DispatchPlan:
    plan = session.get(DispatchPlan, uuid.UUID(str(plan_id)))
    if plan is None or plan.tenant_id != tenant_id:
        raise NotFoundError("dispatch plan not found")
    if plan.version != expected_version:
        raise ScheduleConflictError("dispatch plan was edited concurrently; reload and retry")
    plan.sequence = sequence
    plan.version += 1
    plan.edited_by = edited_by
    plan.edited_at = _now()
    session.flush()
    return plan
