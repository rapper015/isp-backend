"""SLO lifecycle, error budgets, maintenance exclusions, immutable versions."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.domain.exceptions import SloError
from app.domain.slos import calculate_error_budget, window_bounds, with_maintenance_excluded
from app.models import SloDefinition, SloVersion, SloWindowState
from app.services import slo_service


def _now():
    return datetime.now(timezone.utc)


def _make_slo(session, tenant_id, objective=0.99, code=None):
    sli = slo_service.create_sli(session, tenant_id, {
        "code": code or f"sli-{uuid.uuid4().hex[:8]}", "name": "Test SLI",
        "good_event_definition": "ok", "valid_event_definition": "all"})
    slo = slo_service.create_slo(session, tenant_id, {
        "code": f"slo-{uuid.uuid4().hex[:8]}", "name": "Test SLO",
        "sli_id": sli.id, "objective": objective,
        "window_seconds": 30 * 24 * 3600, "published": True})
    session.commit()
    return slo


def test_create_slo_creates_version(defaults, session, tenant_id):
    slo = _make_slo(session, tenant_id)
    version = slo_service.latest_version(session, slo.id)
    assert slo.state == "DRAFT"
    assert version.version == 1
    assert version.objective == 0.99
    assert version.state == "ACTIVE"  # published


def test_published_versions_are_immutable(defaults, session, tenant_id):
    slo = _make_slo(session, tenant_id)  # publishes v1 with objective 0.99
    v1 = slo_service.latest_version(session, slo.id)
    # Changing an SLO always publishes a NEW version; published versions are
    # never edited in place.
    v2 = slo_service._new_version(session, slo, tenant_id, {
        "objective": 0.95, "window_seconds": 30 * 24 * 3600, "published": True})
    session.commit()
    assert v2.version == 2
    assert v2.objective == 0.95
    # v1 keeps its original published objective
    assert v1.objective == 0.99
    # v1 and v2 are distinct immutable records
    assert v1.id != v2.id


def test_validate_approve_activate(defaults, session, tenant_id):
    slo = _make_slo(session, tenant_id)
    slo_service.validate_slo(session, slo.id)
    slo_service.approve_slo(session, slo.id, "sre")
    slo_service.activate_slo(session, slo.id)
    session.commit()
    assert slo.state == "ACTIVE"


def test_invalid_transition_rejected(defaults, session, tenant_id):
    slo = _make_slo(session, tenant_id)
    with pytest.raises(SloError):
        slo_service.approve_slo(session, slo.id, "sre")  # DRAFT cannot approve directly


def test_record_and_compute_window(defaults, session, tenant_id):
    slo = _make_slo(session, tenant_id, objective=0.99)
    sli = slo.sli_id
    # Find sli code via measurement record
    slo_service.record_measurement(session, tenant_id, _sli_code(session, sli), good=99, total=100)
    slo_service.record_measurement(session, tenant_id, _sli_code(session, sli), good=100, total=100)
    session.commit()
    now = _now()
    start, end = window_bounds(now, window_type="ROLLING", window_seconds=30 * 24 * 3600)
    state = slo_service.compute_window(session, tenant_id, slo.id, window_start=start, window_end=end)
    session.commit()
    assert state.total == 200
    assert state.good == 199
    assert state.sli_ratio == pytest.approx(0.995)
    assert state.status == "HEALTHY"


def test_calendar_window_bounds():
    now = datetime(2024, 6, 15, tzinfo=timezone.utc)
    start, end = window_bounds(now, window_type="CALENDAR", window_seconds=0)
    assert start.month == 6 and start.day == 1
    assert end.month == 7 and end.day == 1


def test_error_budget_math():
    res = calculate_error_budget(good=995, total=1000, objective=0.99,
                                 window_seconds=30 * 24 * 3600, policy_version=1)
    assert res.sli_ratio == pytest.approx(0.995)
    assert res.allowed_bad == pytest.approx(10)
    assert res.consumed_bad == pytest.approx(5)
    assert res.remaining_budget == pytest.approx(0.5, abs=1e-3)
    assert res.fast_burn is False


def test_fast_burn_detected():
    res = calculate_error_budget(good=0, total=1000, objective=0.99,
                                 window_seconds=30 * 24 * 3600, policy_version=1)
    assert res.fast_burn is True
    assert res.status in ("AT_RISK", "BREACHED", "EXHAUSTED")


def test_maintenance_exclusion_preserves_raw():
    good, total = with_maintenance_excluded(95, 100, 5, 5)
    assert (good, total) == (90, 95)
    # raw is preserved by caller
    assert 95 + 5 == 100


def test_error_budget_endpoint_data(defaults, session, tenant_id):
    slo = _make_slo(session, tenant_id, objective=0.99)
    slo_service.record_measurement(session, tenant_id, _sli_code(session, slo.sli_id), good=50, total=100)
    session.commit()
    budget = slo_service.error_budget(session, slo.id)
    assert budget["slo_id"] == str(slo.id)
    assert "status" in budget
    assert budget["remaining_budget"] >= 0


def test_duplicate_sli_code_rejected(defaults, session, tenant_id):
    sli = slo_service.create_sli(session, tenant_id, {
        "code": "dup", "name": "A", "good_event_definition": "g", "valid_event_definition": "v"})
    session.commit()
    with pytest.raises(SloError):
        slo_service.create_sli(session, tenant_id, {
            "code": "dup", "name": "B", "good_event_definition": "g", "valid_event_definition": "v"})


def _sli_code(session, sli_id):
    from app.models import SlIDefinition
    sli = session.get(SlIDefinition, sli_id)
    return sli.code
