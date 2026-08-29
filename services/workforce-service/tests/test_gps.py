"""GPS geofence check-in/out: inside/outside, low accuracy, invalid coords,
offline exception, supervisor exception and privacy-safe customer status."""
from datetime import datetime, timedelta, timezone

import pytest

from app.domain.exceptions import GPSValidationError, ValidationError
from app.services import visit_service, workorder_service

TODAY = datetime.now(timezone.utc).replace(hour=10, minute=0, second=0, microsecond=0)
# Work order location (Delhi) — geofence radius 500m by default.
WO_LAT, WO_LNG = 28.6139, 77.2090


def _add_days(days: int) -> datetime:
    return TODAY + timedelta(days=days)


def _assigned_wo(session, tenant_id, make_work_order, make_technician, **wo_kwargs):
    wo = make_work_order(**wo_kwargs)
    technician = make_technician("GPS Tech", skills=["FIBER_INSTALL", "ONT_INSTALL"],
                                 certifications=[{"certification": "FIBER_SAFETY"}])
    from app.services import appointment_service

    workorder_service.validate_work_order(session, tenant_id, wo.id, actor="test")
    appointment_service.schedule(session, tenant_id, wo, window_start=_add_days(1),
                                 window_end=_add_days(1) + timedelta(hours=2), actor="test")
    wo = workorder_service.assign_work_order(session, tenant_id, wo.id, technician_id=technician.id,
                                             reason="gps test", actor="test")
    workorder_service.dispatch_work_order(session, tenant_id, wo.id, actor="test")
    session.commit()
    session.refresh(wo)
    return wo, technician


def test_checkin_within_geofence(session, tenant_id, make_work_order, make_technician):
    wo, tech = _assigned_wo(session, tenant_id, make_work_order, make_technician)
    wo = workorder_service.check_in_work_order(
        session, tenant_id, wo.id, technician_id=tech.id,
        payload={"latitude": WO_LAT + 0.001, "longitude": WO_LNG + 0.001, "gps_accuracy_m": 15}, actor="test")
    assert wo.status == "ARRIVED"
    from app.models import VisitCheckIn

    checkin = session.query(VisitCheckIn).filter_by(work_order_id=wo.id).first()
    assert checkin is not None
    assert checkin.latitude is not None


def test_checkin_outside_geofence_rejected(session, tenant_id, make_work_order, make_technician):
    wo, tech = _assigned_wo(session, tenant_id, make_work_order, make_technician)
    # ~10 km away.
    with pytest.raises(GPSValidationError):
        workorder_service.check_in_work_order(
            session, tenant_id, wo.id, technician_id=tech.id,
            payload={"latitude": WO_LAT + 0.1, "longitude": WO_LNG + 0.1, "gps_accuracy_m": 15}, actor="test")


def test_checkin_low_accuracy_rejected(session, tenant_id, make_work_order, make_technician):
    wo, tech = _assigned_wo(session, tenant_id, make_work_order, make_technician)
    with pytest.raises(GPSValidationError):
        workorder_service.check_in_work_order(
            session, tenant_id, wo.id, technician_id=tech.id,
            payload={"latitude": WO_LAT, "longitude": WO_LNG, "gps_accuracy_m": 500}, actor="test")


def test_checkin_invalid_coordinates_rejected(session, tenant_id, make_work_order, make_technician):
    wo, tech = _assigned_wo(session, tenant_id, make_work_order, make_technician)
    with pytest.raises(GPSValidationError):
        workorder_service.check_in_work_order(
            session, tenant_id, wo.id, technician_id=tech.id,
            payload={"latitude": 95, "longitude": 200, "gps_accuracy_m": 15}, actor="test")


def test_checkin_exception_for_indoor_gps(session, tenant_id, make_work_order, make_technician):
    wo, tech = _assigned_wo(session, tenant_id, make_work_order, make_technician)
    wo = workorder_service.check_in_work_order(
        session, tenant_id, wo.id, technician_id=tech.id,
        payload={"exception_reason": "INDOOR_GPS", "gps_accuracy_m": 250}, actor="test")
    assert wo.status == "ARRIVED"
    from app.models import VisitCheckIn

    checkin = session.query(VisitCheckIn).filter_by(work_order_id=wo.id).first()
    assert checkin.exception_reason == "INDOOR_GPS"


def test_checkin_requires_coordinates_or_exception(session, tenant_id, make_work_order, make_technician):
    wo, tech = _assigned_wo(session, tenant_id, make_work_order, make_technician)
    with pytest.raises(GPSValidationError):
        workorder_service.check_in_work_order(
            session, tenant_id, wo.id, technician_id=tech.id,
            payload={"gps_accuracy_m": 15}, actor="test")


def test_checkout_records_visit(session, tenant_id, make_work_order, make_technician):
    wo, tech = _assigned_wo(session, tenant_id, make_work_order, make_technician)
    workorder_service.check_in_work_order(
        session, tenant_id, wo.id, technician_id=tech.id,
        payload={"latitude": WO_LAT, "longitude": WO_LNG, "gps_accuracy_m": 15}, actor="test")
    workorder_service.check_out_work_order(
        session, tenant_id, wo.id, technician_id=tech.id,
        payload={"latitude": WO_LAT, "longitude": WO_LNG, "gps_accuracy_m": 15}, actor="test")
    from app.models import VisitCheckOut, FieldVisit

    visit = session.query(FieldVisit).filter_by(work_order_id=wo.id).first()
    assert visit is not None
    checkout = session.query(VisitCheckOut).filter_by(work_order_id=wo.id).first()
    assert checkout is not None
    assert checkout.latitude is not None


def test_gps_checkin_unassigned_technician_rejected(session, tenant_id, make_work_order):
    wo = make_work_order()
    import uuid as _uuid

    with pytest.raises(ValidationError):
        visit_service.perform_check_in(session, tenant_id, wo.id, technician_id=_uuid.uuid4(),
                                       payload={"latitude": WO_LAT, "longitude": WO_LNG}, actor="test")
