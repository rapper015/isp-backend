"""Field visit service: check-in/check-out with GPS validation and governed
exceptions.

Each visit may contain one check-in and one final check-out. GPS failures use
an exception workflow (indoor GPS, rural low accuracy, offline, incorrect
location record, infrastructure work outside the customer geofence) instead of
encouraging fabricated coordinates. Supervisor overrides are audited."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.exceptions import GPSValidationError, NotFoundError, ValidationError
from ..domain.gps import get_maps_provider, haversine_distance_m
from ..models import (
    Appointment,
    FieldVisit,
    ServiceArea,
    TimeEntry,
    VisitCheckIn,
    VisitCheckOut,
    WorkOrder,
)
from .audit_service import correlation


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_visit_or_404(session: Session, tenant_id, visit_id: uuid.UUID) -> FieldVisit:
    visit = session.get(FieldVisit, visit_id)
    if visit is None or visit.tenant_id != tenant_id:
        raise NotFoundError("field visit not found")
    return visit


def _latest_visit(session: Session, work_order_id) -> FieldVisit | None:
    return session.scalars(
        select(FieldVisit).where(FieldVisit.work_order_id == work_order_id)
        .order_by(FieldVisit.attempt_number.desc())).first()


def _validate_gps(session: Session, work_order: WorkOrder, payload: dict, *, on_override: bool) -> dict:
    """Validate GPS capture. Returns the accepted coordinate payload."""
    latitude = payload.get("latitude")
    longitude = payload.get("longitude")
    accuracy = payload.get("gps_accuracy_m")
    exception_reason = payload.get("exception_reason")
    distance_from_expected = payload.get("distance_from_expected_m")

    has_coords = latitude is not None and longitude is not None
    if not has_coords:
        if exception_reason:
            return {"latitude": None, "longitude": None, "gps_accuracy_m": accuracy,
                    "distance_from_expected_m": None, "exception_reason": exception_reason}
        raise GPSValidationError("GPS coordinates required (or record an exception reason)")

    # Basic bounds validation.
    if not (-90 <= latitude <= 90) or not (-180 <= longitude <= 180):
        raise GPSValidationError("invalid GPS coordinates")

    if accuracy is not None and accuracy > 100 and not exception_reason:
        raise GPSValidationError("GPS accuracy too low (require exception)")

    # Geofence check against the service area / work order location.
    if work_order.latitude is not None and work_order.longitude is not None:
        distance = haversine_distance_m(work_order.latitude, work_order.longitude, latitude, longitude)
        radius = 500  # default geofence radius (m); service area may override
        if work_order.service_area_id:
            area = session.get(ServiceArea, work_order.service_area_id)
            if area is not None:
                radius = area.geofence_radius_m or radius
        if distance > radius and not exception_reason:
            raise GPSValidationError(f"location {distance:.0f}m from expected location exceeds geofence ({radius}m)")
        distance_from_expected = distance

    return {"latitude": latitude, "longitude": longitude, "gps_accuracy_m": accuracy,
            "distance_from_expected_m": round(distance_from_expected, 1) if distance_from_expected is not None else None,
            "exception_reason": exception_reason}


def _service_area(session, service_area_id):
    if service_area_id is None:
        return None
    return session.get(ServiceArea, service_area_id)


def perform_check_in(session: Session, tenant_id, work_order_id: uuid.UUID, *, technician_id: uuid.UUID,
                     payload: dict, actor: str = "system", correlation_id: str | None = None,
                     device_ref: str | None = None) -> tuple[FieldVisit, VisitCheckIn]:
    work_order = session.get(WorkOrder, work_order_id)
    if work_order is None or work_order.tenant_id != tenant_id:
        raise NotFoundError("work order not found")
    if str(work_order.assigned_technician_id) != str(technician_id):
        raise ValidationError("technician is not assigned to this work order")

    # Appointment must be confirmed (or supervisor override for legitimate cases).
    if work_order.current_appointment_id:
        appointment = session.get(Appointment, work_order.current_appointment_id)
        if appointment is not None and appointment.status not in ("CONFIRMED", "TECHNICIAN_DISPATCHED") \
                and not payload.get("override_approved_by"):
            raise ValidationError("appointment is not confirmed")

    gps = _validate_gps(session, work_order, payload, on_override=bool(payload.get("override_approved_by")))
    existing_visits = session.scalar(select(FieldVisit.attempt_number).where(
        FieldVisit.work_order_id == work_order.id).order_by(FieldVisit.attempt_number.desc())) or 0
    visit = FieldVisit(
        tenant_id=tenant_id, work_order_id=work_order.id, appointment_id=work_order.current_appointment_id,
        attempt_number=existing_visits + 1, status="ON_SITE", technician_id=technician_id,
        started_at=_now(), correlation_id=correlation_id or correlation(None),
    )
    session.add(visit)
    session.flush()

    checkin = VisitCheckIn(
        tenant_id=tenant_id, work_order_id=work_order.id, visit_id=visit.id,
        device_timestamp=payload.get("device_timestamp"), latitude=gps.get("latitude"),
        longitude=gps.get("longitude"), gps_accuracy_m=gps.get("gps_accuracy_m"),
        distance_from_expected_m=gps.get("distance_from_expected_m"),
        device_ref=device_ref, source=("OFFLINE" if payload.get("offline") else "ONLINE"),
        network_available=bool(payload.get("network_available", True)),
        exception_reason=gps.get("exception_reason"), override_approved_by=payload.get("override_approved_by"),
        actor=actor, correlation_id=correlation_id or correlation(None),
    )
    session.add(checkin)
    session.flush()
    return visit, checkin


def perform_check_out(session: Session, tenant_id, work_order_id: uuid.UUID, *, technician_id: uuid.UUID,
                      payload: dict, actor: str = "system", correlation_id: str | None = None,
                      device_ref: str | None = None) -> tuple[FieldVisit, VisitCheckOut]:
    work_order = session.get(WorkOrder, work_order_id)
    if work_order is None or work_order.tenant_id != tenant_id:
        raise NotFoundError("work order not found")
    if str(work_order.assigned_technician_id) != str(technician_id):
        raise ValidationError("technician is not assigned to this work order")
    visit = _latest_visit(session, work_order_id)
    if visit is None or visit.status == "COMPLETED":
        raise ValidationError("no active visit to check out")
    gps = _validate_gps(session, work_order, payload, on_override=bool(payload.get("override_approved_by")))
    checkout = VisitCheckOut(
        tenant_id=tenant_id, work_order_id=work_order.id, visit_id=visit.id,
        device_timestamp=payload.get("device_timestamp"), latitude=gps.get("latitude"),
        longitude=gps.get("longitude"), gps_accuracy_m=gps.get("gps_accuracy_m"),
        device_ref=device_ref, source=("OFFLINE" if payload.get("offline") else "ONLINE"),
        exception_reason=gps.get("exception_reason"), override_approved_by=payload.get("override_approved_by"),
        actor=actor, correlation_id=correlation_id or correlation(None),
    )
    session.add(checkout)
    visit.status = "COMPLETED"
    visit.ended_at = _now()
    if visit.started_at:
        started_at = visit.started_at
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        duration = int((_now() - started_at).total_seconds())
        session.add(TimeEntry(tenant_id=tenant_id, technician_id=technician_id, work_order_id=work_order.id,
                              visit_id=visit.id, entry_type="WORK", started_at=visit.started_at, ended_at=_now(),
                              duration_seconds=duration, source="MOBILE"))
    session.flush()
    return visit, checkout


def visits_for_work_order(session: Session, tenant_id, work_order_id) -> list[FieldVisit]:
    return list(session.scalars(
        select(FieldVisit).where(FieldVisit.work_order_id == work_order_id,
                                 FieldVisit.tenant_id == tenant_id).order_by(FieldVisit.attempt_number)))
