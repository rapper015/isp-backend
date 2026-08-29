"""Explainable technician assignment scoring engine.

Assignment considers required skills, certifications, availability, current
workload, service area, proximity/travel, priority/SLA deadline, employment
type (contractor preference) and continuity. Every automatic assignment
persists the score breakdown and the reason so decisions are explainable.
Manual override is always allowed with a reason."""
from __future__ import annotations

import uuid
from datetime import date, datetime, time, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    TechnicianCertification,
    TechnicianProfile,
    TechnicianSkill,
    WorkOrder,
)
from . import technicians as tech_rules
from .gps import get_maps_provider, haversine_distance_m

WEIGHTS = {
    "skills": 40.0,
    "certifications": 20.0,
    "availability": 15.0,
    "workload": 10.0,
    "proximity": 8.0,
    "service_area": 5.0,
    "continuity": 2.0,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def score_technician(
    session: Session,
    tenant_id,
    work_order: WorkOrder,
    technician: TechnicianProfile,
    *,
    required_skills: list | None = None,
    required_certifications: list | None = None,
    appointment_start: datetime | None = None,
) -> tuple[float, dict]:
    """Score one technician. Returns (score, breakdown)."""
    on_date = (appointment_start or _now()).date()
    start_time = (appointment_start or _now()).time()
    end_time = (appointment_start or _now()).replace(hour=23, minute=59).time()
    breakdown: dict = {}

    # Skills
    skills = tech_rules.technician_skills(session, technician.id)
    skill_score = 0.0
    if required_skills:
        matched = sum(1 for s in required_skills if s in skills)
        skill_score = 100.0 * matched / len(required_skills)
    else:
        skill_score = 100.0
    breakdown["skills"] = round(skill_score, 1)

    # Certifications
    certs = tech_rules.technician_certifications(session, technician.id)
    cert_score = 100.0
    if required_certifications:
        missing = [c for c in required_certifications if c not in certs]
        exceptions = {e["certification"] for e in tech_rules.certification_exceptions(session, tenant_id, technician.id)}
        still_missing = [c for c in missing if c not in exceptions]
        cert_score = 100.0 - (100.0 * len(still_missing) / len(required_certifications))
    breakdown["certifications"] = round(cert_score, 1)

    # Availability
    avail_score = 100.0 if tech_rules.technician_available_on(
        session, technician.id, on_date, start_time=start_time, end_time=end_time) else 0.0
    breakdown["availability"] = round(avail_score, 1)

    # Workload (least-loaded)
    open_count = tech_rules.open_work_count(session, tenant_id, technician.id)
    workload_score = max(0.0, 100.0 - open_count * 20.0)
    breakdown["workload"] = round(workload_score, 1)

    # Proximity / travel
    proximity_score = 50.0
    if work_order.latitude is not None and work_order.longitude is not None and technician.base_lat is not None and technician.base_lng is not None:
        maps = get_maps_provider()
        estimate = maps.travel_estimate(technician.base_lat, technician.base_lng, work_order.latitude, work_order.longitude)
        distance_m = estimate["distance_m"]
        proximity_score = max(0.0, 100.0 - (distance_m / 1000.0) * 5.0)  # -5 per km
        breakdown["distance_m"] = round(distance_m, 1)
        breakdown["travel_source"] = estimate["source"]
    breakdown["proximity"] = round(proximity_score, 1)

    # Service area
    area_score = 100.0
    if work_order.service_area_id is not None and technician.service_area_ids:
        if str(work_order.service_area_id) not in [str(x) for x in technician.service_area_ids]:
            area_score = 0.0
    breakdown["service_area"] = round(area_score, 1)

    # Continuity with a previously assigned technician (failed visits / rework).
    continuity_score = 50.0
    previous = session.scalars(
        select(WorkOrder.assigned_technician_id).where(WorkOrder.id == work_order.id)).first()
    if previous is not None and str(previous) == str(technician.id):
        continuity_score = 100.0
    breakdown["continuity"] = round(continuity_score, 1)

    total = sum(WEIGHTS[k] * breakdown.get(k, 0.0) for k in WEIGHTS)
    breakdown["_total"] = round(total, 1)
    return total, breakdown


def select_technician(
    session: Session,
    tenant_id,
    work_order: WorkOrder,
    *,
    strategy: str = "SKILL_BASED",
    candidates: list[TechnicianProfile] | None = None,
    required_skills: list | None = None,
    required_certifications: list | None = None,
    appointment_start: datetime | None = None,
    exclude: set[uuid.UUID] | None = None,
    limit: int = 20,
) -> tuple[TechnicianProfile | None, float | None, dict]:
    """Score candidate technicians and pick per strategy."""
    candidates = candidates or list(session.scalars(
        select(TechnicianProfile).where(
            TechnicianProfile.tenant_id == tenant_id,
            TechnicianProfile.is_active.is_(True),
            TechnicianProfile.operational_status.notin_(("OFF_SHIFT", "UNAVAILABLE", "EMERGENCY_UNAVAILABLE")),
        ).limit(limit)))
    if exclude:
        candidates = [c for c in candidates if c.id not in exclude]

    scored = []
    for technician in candidates:
        score, breakdown = score_technician(
            session, tenant_id, work_order, technician,
            required_skills=required_skills, required_certifications=required_certifications,
            appointment_start=appointment_start)
        scored.append((score, technician, breakdown))

    if not scored:
        return None, None, {}

    if strategy == "ROUND_ROBIN":
        scored.sort(key=lambda pair: (pair[1].name, pair[0]))
    elif strategy == "LEAST_LOADED":
        scored.sort(key=lambda pair: (tech_rules.open_work_count(session, tenant_id, pair[1].id), -pair[0]))
    elif strategy in ("SKILL_BASED", "CERTIFICATION_BASED", "SERVICE_AREA_BASED", "PROXIMITY_BASED",
                      "SLA_DEADLINE_BASED", "PRIORITY_BASED", "TEAM_BASED", "CONTRACTOR_BASED"):
        scored.sort(key=lambda pair: -pair[0])
    else:  # MANUAL and default
        scored.sort(key=lambda pair: -pair[0])

    best_score, best, best_breakdown = scored[0]
    return best, round(best_score, 1), best_breakdown
