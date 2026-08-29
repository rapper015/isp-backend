"""Assignment/scheduling rules: skills, certifications (incl. expired), shift
availability, capacity, service area, travel proximity and manual override."""
import uuid as _uuid
from datetime import date, datetime, time, timedelta, timezone

import pytest

from app.domain.assignment import score_technician, select_technician
from app.domain import technicians as tech_rules
from app.domain.exceptions import NotFoundError, ValidationError
from app.services import appointment_service, dispatch_service, technician_service, workorder_service

TODAY = datetime.now(timezone.utc).replace(hour=10, minute=0, second=0, microsecond=0)


def _add_days(days: int) -> datetime:
    return TODAY + timedelta(days=days)


def _ready_wo(session, tenant_id, make_work_order):
    wo = make_work_order()
    return workorder_service.validate_work_order(session, tenant_id, wo.id, actor="test")


def _skill_tech(make_technician, **kw):
    return make_technician("Skill Tech", skills=["FIBER_INSTALL", "ONT_INSTALL"],
                           certifications=[{"certification": "FIBER_SAFETY"}], **kw)


def test_select_prefers_skilled_technician(session, tenant_id, make_work_order, make_technician):
    wo = _ready_wo(session, tenant_id, make_work_order)
    skilled = _skill_tech(make_technician)
    unskilled = make_technician("Unskilled Tech", skills=["SURVEY"])
    selected, score, breakdown = select_technician(session, tenant_id, wo,
                                                   required_skills=["FIBER_INSTALL", "ONT_INSTALL"],
                                                   required_certifications=["FIBER_SAFETY"])
    assert selected is not None and str(selected.id) == str(skilled.id)
    assert breakdown["skills"] == 100.0
    assert score > 0


def test_skills_and_cert_breakdown(session, tenant_id, make_work_order, make_technician):
    wo = _ready_wo(session, tenant_id, make_work_order)
    tech = make_technician("Partial Tech", skills=["FIBER_INSTALL"],
                           certifications=[{"certification": "FIBER_SAFETY"}])
    score, breakdown = score_technician(session, tenant_id, wo, tech,
                                        required_skills=["FIBER_INSTALL", "ONT_INSTALL"],
                                        required_certifications=["FIBER_SAFETY"])
    assert breakdown["skills"] == 50.0
    assert breakdown["certifications"] == 100.0


def test_expired_cert_blocks_assignment(session, tenant_id, make_work_order, make_technician):
    wo = _ready_wo(session, tenant_id, make_work_order)
    past = date.today() - timedelta(days=1)
    tech = make_technician("Expired Cert", skills=["FIBER_INSTALL", "ONT_INSTALL"],
                           certifications=[{"certification": "FIBER_SAFETY", "expires_at": past}])
    eligible, missing, reasons = tech_rules.meets_requirements(
        session, tenant_id, tech.id, required_skills=["FIBER_INSTALL", "ONT_INSTALL"],
        required_certifications=["FIBER_SAFETY"])
    assert eligible is False
    assert "certifications" in missing


def test_cert_exception_permits_assignment(session, tenant_id, make_work_order, make_technician):
    wo = _ready_wo(session, tenant_id, make_work_order)
    past = date.today() - timedelta(days=2)
    tech = make_technician("Exception Tech", skills=["FIBER_INSTALL", "ONT_INSTALL"],
                           certifications=[{"certification": "FIBER_SAFETY", "expires_at": past}])
    tech_rules.add_certification_exception(session, tenant_id, tech.id, "FIBER_SAFETY",
                                           reason="supervisor approved", approved_by="supervisor-1")
    session.commit()
    eligible, missing, reasons = tech_rules.meets_requirements(
        session, tenant_id, tech.id, required_skills=["FIBER_INSTALL", "ONT_INSTALL"],
        required_certifications=["FIBER_SAFETY"])
    assert eligible is True


def test_off_shift_technician_not_available(session, tenant_id, make_work_order, make_technician):
    wo = _ready_wo(session, tenant_id, make_work_order)
    tech = make_technician("OffShift", skills=["FIBER_INSTALL", "ONT_INSTALL"],
                           certifications=[{"certification": "FIBER_SAFETY"}], available=False)
    session.commit()
    eligible, missing, reasons = tech_rules.meets_requirements(
        session, tenant_id, tech.id, required_skills=["FIBER_INSTALL", "ONT_INSTALL"],
        required_certifications=["FIBER_SAFETY"])
    assert eligible is False
    assert "availability" in missing or "unavailable" in missing


def test_service_area_restricts_technician(session, tenant_id, make_work_order, make_technician):
    from app.models import ServiceArea

    area = ServiceArea(tenant_id=tenant_id, name="Zone A", code="ZONE-A", geofence_radius_m=1000)
    session.add(area)
    session.flush()
    session.commit()

    wo = _ready_wo(session, tenant_id, make_work_order)
    wo.service_area_id = area.id
    session.commit()

    inside = make_technician("Inside", skills=["FIBER_INSTALL", "ONT_INSTALL"],
                             certifications=[{"certification": "FIBER_SAFETY"}], service_area_ids=[area.id])
    outside = make_technician("Outside", skills=["FIBER_INSTALL", "ONT_INSTALL"],
                              certifications=[{"certification": "FIBER_SAFETY"}],
                              service_area_ids=[_uuid.uuid4()])

    eligible_in, _, _ = tech_rules.meets_requirements(
        session, tenant_id, inside.id, required_skills=["FIBER_INSTALL", "ONT_INSTALL"],
        required_certifications=["FIBER_SAFETY"], service_area_id=area.id)
    eligible_out, _, _ = tech_rules.meets_requirements(
        session, tenant_id, outside.id, required_skills=["FIBER_INSTALL", "ONT_INSTALL"],
        required_certifications=["FIBER_SAFETY"], service_area_id=area.id)
    assert eligible_in is True
    assert eligible_out is False


def test_capacity_limits_daily_load(session, tenant_id, make_work_order, make_technician):
    tech = _skill_tech(make_technician, capacity=2)
    # Assign two work orders to fill capacity.
    for _ in range(2):
        wo = _ready_wo(session, tenant_id, make_work_order)
        appointment_service.schedule(session, tenant_id, wo, window_start=_add_days(1),
                                     window_end=_add_days(1) + timedelta(hours=2), actor="test")
        workorder_service.assign_work_order(session, tenant_id, wo.id, technician_id=tech.id,
                                            reason="fill capacity", actor="test")
    session.commit()
    from app.domain.technicians import open_work_count

    assert open_work_count(session, tenant_id, tech.id) == 2
    # The third work order should prefer another technician (capacity warning path).
    wo3 = _ready_wo(session, tenant_id, make_work_order)
    selected, score, breakdown = select_technician(session, tenant_id, wo3,
                                                   required_skills=["FIBER_INSTALL", "ONT_INSTALL"],
                                                   required_certifications=["FIBER_SAFETY"])
    # Only tech exists; workload component should be penalized but still assignable.
    assert selected is not None
    assert breakdown["workload"] == 60.0


def test_manual_override_selects_specific_technician(session, tenant_id, make_work_order, make_technician):
    wo = _ready_wo(session, tenant_id, make_work_order)
    tech = make_technician("Manual Pick", skills=["FIBER_INSTALL", "ONT_INSTALL"],
                           certifications=[{"certification": "FIBER_SAFETY"}])
    appointment_service.schedule(session, tenant_id, wo, window_start=_add_days(1),
                                 window_end=_add_days(1) + timedelta(hours=2), actor="test")
    wo = workorder_service.assign_work_order(session, tenant_id, wo.id, technician_id=tech.id,
                                             reason="manual selection", actor="test")
    assert str(wo.assigned_technician_id) == str(tech.id)


def test_assign_unknown_technician_404(session, tenant_id, make_work_order):
    import uuid as _uuid

    wo = _ready_wo(session, tenant_id, make_work_order)
    with pytest.raises(NotFoundError):
        workorder_service.assign_work_order(session, tenant_id, wo.id,
                                            technician_id=_uuid.uuid4(), reason="test", actor="test")


def test_conflict_detection_in_dispatch(session, tenant_id, make_work_order, make_technician):
    tech = _skill_tech(make_technician)
    # First work order occupies tomorrow 10:00-12:00.
    wo1 = _ready_wo(session, tenant_id, make_work_order)
    appointment_service.schedule(session, tenant_id, wo1, window_start=_add_days(1),
                                 window_end=_add_days(1) + timedelta(hours=2), actor="test")
    workorder_service.assign_work_order(session, tenant_id, wo1.id, technician_id=tech.id,
                                        reason="first", actor="test")
    session.commit()

    # Second work order overlaps the same window -> conflict.
    wo2 = _ready_wo(session, tenant_id, make_work_order)
    result = dispatch_service.validate_assignment(
        session, tenant_id, wo2.id, tech.id, window_start=_add_days(1),
        window_end=_add_days(1) + timedelta(hours=2))
    assert result["eligible"] is False
    assert result.get("conflicts")


def test_no_conflict_for_non_overlapping_windows(session, tenant_id, make_work_order, make_technician):
    tech = _skill_tech(make_technician)
    wo1 = _ready_wo(session, tenant_id, make_work_order)
    appointment_service.schedule(session, tenant_id, wo1, window_start=_add_days(1),
                                 window_end=_add_days(1) + timedelta(hours=2), actor="test")
    workorder_service.assign_work_order(session, tenant_id, wo1.id, technician_id=tech.id,
                                        reason="first", actor="test")
    session.commit()
    wo2 = _ready_wo(session, tenant_id, make_work_order)
    result = dispatch_service.validate_assignment(
        session, tenant_id, wo2.id, tech.id, window_start=_add_days(1) + timedelta(hours=4),
        window_end=_add_days(1) + timedelta(hours=6))
    assert result["eligible"] is True
    assert not result.get("conflicts")


def test_route_sequence_build(session, tenant_id, make_work_order, make_technician):
    tech = _skill_tech(make_technician)
    wos = []
    for _ in range(2):
        wo = _ready_wo(session, tenant_id, make_work_order)
        appointment_service.schedule(session, tenant_id, wo, window_start=_add_days(1),
                                     window_end=_add_days(1) + timedelta(hours=2), actor="test")
        workorder_service.assign_work_order(session, tenant_id, wo.id, technician_id=tech.id,
                                            reason="route test", actor="test")
        wos.append(wo)
    session.commit()
    from app.domain.dispatch import build_route_sequence

    route = build_route_sequence(session, tenant_id, tech, wos)
    assert isinstance(route, list)
    assert len(route) == 2
    assert all("work_order_id" in step for step in route)


def test_bulk_preview(session, tenant_id, make_work_order, make_technician):
    tech = _skill_tech(make_technician)
    wos = [_ready_wo(session, tenant_id, make_work_order) for _ in range(2)]
    previews = dispatch_service.bulk_assignment_preview(session, tenant_id, [wo.id for wo in wos])
    assert len(previews) == 2
    assert all(p.get("suggested_technician_id") is not None for p in previews)
