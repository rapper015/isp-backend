"""Technician profile service: profiles, skills, certifications, availability,
shifts and operational status transitions."""
from __future__ import annotations

import uuid
from datetime import date, datetime, time, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.exceptions import NotFoundError, ValidationError
from ..enums import EMPLOYMENT_TYPES, TECHNICIAN_STATUSES
from ..models import (
    TechnicianAvailability,
    TechnicianCertification,
    TechnicianProfile,
    TechnicianShift,
    TechnicianSkill,
    TechnicianStatusLog,
)
from .audit_service import audit, correlation


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_technician_or_404(session: Session, tenant_id, technician_id: uuid.UUID) -> TechnicianProfile:
    technician = session.get(TechnicianProfile, technician_id)
    if technician is None or technician.tenant_id != tenant_id:
        raise NotFoundError("technician not found")
    return technician


def create_technician(session: Session, tenant_id, *, user_ref: str, name: str, phone: str | None = None,
                      email: str | None = None, employment_type: str = "EMPLOYEE", team_code: str | None = None,
                      supervisor_ref: str | None = None, base_lat: float | None = None, base_lng: float | None = None,
                      vehicle_ref: str | None = None, max_daily_capacity: int = 4,
                      supported_work_order_types: list | None = None, service_area_ids: list | None = None,
                      actor: str | None = None) -> TechnicianProfile:
    employment_type = employment_type.upper()
    if employment_type not in EMPLOYMENT_TYPES:
        raise ValidationError(f"invalid employment type {employment_type!r}")
    technician = TechnicianProfile(
        tenant_id=tenant_id, user_ref=user_ref, name=name, phone=phone, email=email,
        employment_type=employment_type, team_code=team_code, supervisor_ref=supervisor_ref,
        base_lat=base_lat, base_lng=base_lng, vehicle_ref=vehicle_ref,
        max_daily_capacity=max_daily_capacity,
        supported_work_order_types=supported_work_order_types or [],
        service_area_ids=[str(x) for x in (service_area_ids or [])],
        operational_status="OFF_SHIFT", is_active=True,
    )
    session.add(technician)
    session.flush()
    audit(session, tenant_id, "workforce.technician.created", "technician", str(technician.id), actor=actor,
          correlation_id=correlation(None), safe_after={"user_ref": user_ref, "name": name})
    return technician


def add_skill(session: Session, tenant_id, technician_id: uuid.UUID, *, skill: str, proficiency: int = 3,
              actor: str | None = None) -> TechnicianSkill:
    technician = get_technician_or_404(session, tenant_id, technician_id)
    row = session.scalars(select(TechnicianSkill).where(
        TechnicianSkill.technician_id == technician.id, TechnicianSkill.skill == skill)).first()
    if row is not None:
        row.proficiency = proficiency
        return row
    row = TechnicianSkill(tenant_id=tenant_id, technician_id=technician.id, skill=skill, proficiency=proficiency)
    session.add(row)
    session.flush()
    return row


def add_certification(session: Session, tenant_id, technician_id: uuid.UUID, *, certification: str,
                      expires_at: date | None = None, actor: str | None = None) -> TechnicianCertification:
    technician = get_technician_or_404(session, tenant_id, technician_id)
    row = TechnicianCertification(tenant_id=tenant_id, technician_id=technician.id, certification=certification,
                                  expires_at=expires_at, is_active=True)
    session.add(row)
    session.flush()
    audit(session, tenant_id, "workforce.technician.certification_added", "technician", str(technician.id),
          actor=actor, correlation_id=correlation(None), safe_after={"certification": certification, "expires_at": str(expires_at)})
    return row


def set_availability(session: Session, tenant_id, technician_id: uuid.UUID, *, available_date: date,
                     start_time: time | None = None, end_time: time | None = None, status: str = "AVAILABLE",
                     actor: str | None = None) -> TechnicianAvailability:
    technician = get_technician_or_404(session, tenant_id, technician_id)
    row = session.scalars(select(TechnicianAvailability).where(
        TechnicianAvailability.technician_id == technician.id,
        TechnicianAvailability.available_date == available_date)).first()
    if row is None:
        row = TechnicianAvailability(tenant_id=tenant_id, technician_id=technician.id, available_date=available_date)
        session.add(row)
    row.start_time = start_time
    row.end_time = end_time
    row.status = status.upper()
    session.flush()
    return row


def set_shift(session: Session, tenant_id, technician_id: uuid.UUID, *, day_of_week: int,
              start_time: time, end_time: time, actor: str | None = None) -> TechnicianShift:
    technician = get_technician_or_404(session, tenant_id, technician_id)
    if not 0 <= day_of_week <= 6:
        raise ValidationError("day_of_week must be 0 (Mon) .. 6 (Sun)")
    row = session.scalars(select(TechnicianShift).where(
        TechnicianShift.technician_id == technician.id, TechnicianShift.day_of_week == day_of_week)).first()
    if row is None:
        row = TechnicianShift(tenant_id=tenant_id, technician_id=technician.id, day_of_week=day_of_week)
        session.add(row)
    row.start_time = start_time
    row.end_time = end_time
    session.flush()
    return row


def transition_status(session: Session, tenant_id, technician_id: uuid.UUID, *, to_status: str,
                      work_order_id: uuid.UUID | None = None, source: str = "API", actor: str | None = None,
                      correlation_id: str | None = None) -> TechnicianProfile:
    technician = get_technician_or_404(session, tenant_id, technician_id)
    to_status = to_status.upper()
    if to_status not in TECHNICIAN_STATUSES:
        raise ValidationError(f"invalid technician status {to_status!r}")
    if technician.operational_status == to_status:
        return technician
    previous = technician.operational_status
    technician.operational_status = to_status
    session.add(TechnicianStatusLog(
        tenant_id=tenant_id, technician_id=technician.id, from_status=previous, to_status=to_status,
        work_order_id=work_order_id, source=source, actor=actor, correlation_id=correlation_id or correlation(None)))
    session.flush()
    return technician


def expire_stale_certifications(session: Session, tenant_id) -> list[str]:
    """Deactivate certifications past their expiry (repair/periodic task)."""
    expired = []
    today = date.today()
    rows = list(session.scalars(
        select(TechnicianCertification).where(TechnicianCertification.tenant_id == tenant_id,
                                              TechnicianCertification.is_active.is_(True),
                                              TechnicianCertification.expires_at.is_not(None))))
    for row in rows:
        if row.expires_at < today:
            row.is_active = False
            expired.append(f"{row.technician_id}:{row.certification}")
    session.flush()
    return expired
