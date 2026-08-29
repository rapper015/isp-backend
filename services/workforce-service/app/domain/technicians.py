"""Technician domain rules: skill/certification matching, availability,
capacity and assignment eligibility.

Expired certifications block assignments that require them unless an authorized
supervisor records an exception. Technician operational status is a separate
concern from work-order state."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    TechnicianAvailability,
    TechnicianCertification,
    TechnicianProfile,
    TechnicianShift,
    TechnicianSkill,
    WorkOrder,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def open_work_count(session: Session, tenant_id, technician_id) -> int:
    return session.scalar(
        select(func.count(WorkOrder.id)).where(
            WorkOrder.tenant_id == tenant_id,
            WorkOrder.assigned_technician_id == technician_id,
            WorkOrder.status.in_(("CREATED", "VALIDATING", "READY_FOR_SCHEDULING", "SCHEDULED", "ASSIGNED",
                                  "DISPATCHED", "EN_ROUTE", "ARRIVED", "IN_PROGRESS", "PAUSED", "BLOCKED",
                                  "AWAITING_PARTS", "AWAITING_REMOTE_ACTION")),
        )
    ) or 0


def technician_skills(session: Session, technician_id) -> dict[str, int]:
    return {row.skill: row.proficiency for row in session.scalars(
        select(TechnicianSkill).where(TechnicianSkill.technician_id == technician_id))}


def technician_certifications(session: Session, technician_id, *, on_date: date | None = None) -> dict[str, date | None]:
    on_date = on_date or date.today()
    result: dict[str, date | None] = {}
    for row in session.scalars(select(TechnicianCertification).where(
            TechnicianCertification.technician_id == technician_id,
            TechnicianCertification.is_active.is_(True))):
        if row.expires_at is None or row.expires_at >= on_date:
            result[row.certification] = row.expires_at
    return result


def technician_available_on(session: Session, technician_id, on_date: date, *, start_time: time, end_time: time) -> bool:
    """True when the technician is on shift and not marked unavailable that day."""
    availability = session.scalars(
        select(TechnicianAvailability).where(
            TechnicianAvailability.technician_id == technician_id,
            TechnicianAvailability.available_date == on_date)).first()
    if availability is not None and availability.status in ("UNAVAILABLE", "ON_LEAVE"):
        return False
    shift = session.scalars(
        select(TechnicianShift).where(TechnicianShift.technician_id == technician_id,
                                      TechnicianShift.day_of_week == on_date.weekday())).first()
    if shift is None:
        return False
    # Window overlaps shift?
    if start_time < shift.end_time and end_time > shift.start_time:
        return True
    return False


def current_workload_seconds(session: Session, technician_id, on_date: date) -> int:
    """Sum of expected durations of open work orders on the given date."""
    day_start = datetime.combine(on_date, time(0, 0), tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)
    rows = list(session.scalars(
        select(WorkOrder).where(WorkOrder.assigned_technician_id == technician_id,
                                WorkOrder.scheduled_start >= day_start,
                                WorkOrder.scheduled_start < day_end)))
    return sum((r.expected_duration_minutes or 60) * 60 for r in rows)


def certification_exceptions(session: Session, tenant_id, technician_id) -> list:
    """Return active supervisor-approved certification exceptions."""
    profile = session.get(TechnicianProfile, technician_id)
    if profile is None:
        return []
    return list(profile.extra.get("certification_exceptions", []))


def add_certification_exception(session: Session, tenant_id, technician_id, certification: str,
                                *, reason: str, approved_by: str) -> dict:
    profile = session.get(TechnicianProfile, technician_id)
    if profile is None:
        from ..domain.exceptions import NotFoundError

        raise NotFoundError("technician not found")
    exceptions = list(profile.extra.get("certification_exceptions", []))
    exceptions.append({"certification": certification, "reason": reason, "approved_by": approved_by,
                       "approved_at": _now().isoformat()})
    profile.extra = {**(profile.extra or {}), "certification_exceptions": exceptions}
    session.flush()
    return {"certification": certification, "approved_by": approved_by}


def meets_requirements(session: Session, tenant_id, technician_id, *, required_skills: list | None = None,
                       required_certifications: list | None = None, work_order_type: str | None = None,
                       service_area_id=None, on_date: date | None = None,
                       start_time: time | None = None, end_time: time | None = None) -> tuple[bool, list[str], dict]:
    """Return (eligible, missing, reasons)."""
    profile = session.get(TechnicianProfile, technician_id)
    if profile is None or not profile.is_active:
        return False, ["inactive"], {"technician": False}
    if profile.operational_status in ("OFF_SHIFT", "UNAVAILABLE", "EMERGENCY_UNAVAILABLE"):
        return False, ["unavailable"], {"status": profile.operational_status}

    missing: list[str] = []
    reasons: dict = {}

    if required_skills:
        skills = technician_skills(session, technician_id)
        missing_skills = [s for s in required_skills if s not in skills]
        if missing_skills:
            missing.append("skills")
            reasons["missing_skills"] = missing_skills

    if required_certifications:
        certs = technician_certifications(session, technician_id)
        expired = [c for c in required_certifications if c not in certs]
        if expired:
            exceptions = {e["certification"] for e in certification_exceptions(session, tenant_id, technician_id)}
            still_missing = [c for c in expired if c not in exceptions]
            if still_missing:
                missing.append("certifications")
                reasons["missing_certifications"] = still_missing

    if service_area_id is not None and profile.service_area_ids:
        if str(service_area_id) not in [str(x) for x in profile.service_area_ids]:
            missing.append("service_area")
            reasons["service_area"] = str(service_area_id)

    if on_date is not None and start_time is not None and end_time is not None:
        if not technician_available_on(session, technician_id, on_date, start_time=start_time, end_time=end_time):
            missing.append("availability")
            reasons["availability"] = "not on shift or unavailable"

    return (not missing), missing, reasons
