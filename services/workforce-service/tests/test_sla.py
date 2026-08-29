"""Field SLA: policy selection, business calendar, pause/resume, at-risk/breach
evaluation, worker restart idempotency and supervisor exceptions."""
from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy import select

from app.domain.sla.calendar import business_seconds_between, deadline_after, default_working_hours
from app.domain.sla.engine import (
    evaluate_field_sla,
    instantiate_field_sla,
    pause_field_sla,
    resume_field_sla,
    select_policy,
)
from app.models import FieldSLAInstance, FieldSLAPause
from app.services import catalog_service, sla_service, workorder_service

TODAY = datetime.now(timezone.utc).replace(hour=10, minute=0, second=0, microsecond=0)


def _add_hours(hours: int) -> datetime:
    return TODAY + timedelta(hours=hours)


def _calendar(session, tenant_id):
    return catalog_service.get_or_create_calendar(session, tenant_id)


def test_business_seconds_excludes_sunday_and_off_hours():
    # Saturday is open 10:00-14:00; Sunday is closed.
    from zoneinfo import ZoneInfo

    hours = default_working_hours()
    # Saturday 14:00 -> Monday 09:00 has no business seconds (rest of Sat off, Sun closed).
    sat = datetime(2026, 1, 10, 14, 0, tzinfo=timezone.utc)
    mon = datetime(2026, 1, 12, 9, 0, tzinfo=timezone.utc)
    assert business_seconds_between(None, "calendar", hours, ZoneInfo("UTC"), [], sat, mon) == 0
    # Friday 18:00 -> Monday 09:00 includes Saturday's 4 business hours.
    fri = datetime(2026, 1, 9, 18, 0, tzinfo=timezone.utc)
    assert business_seconds_between(None, "calendar", hours, ZoneInfo("UTC"), [], fri, mon) == 4 * 3600


def test_business_seconds_same_day():
    from zoneinfo import ZoneInfo

    hours = default_working_hours()
    start = datetime(2026, 1, 12, 9, 0, tzinfo=timezone.utc)  # Monday
    end = datetime(2026, 1, 12, 11, 0, tzinfo=timezone.utc)
    assert business_seconds_between(None, "calendar", hours, ZoneInfo("UTC"), [], start, end) == 2 * 3600


def test_deadline_after_working_hours(session, tenant_id, defaults):
    calendar = _calendar(session, tenant_id)
    from zoneinfo import ZoneInfo

    start = datetime(2026, 1, 12, 9, 0, tzinfo=timezone.utc)  # Monday
    deadline = deadline_after(session, calendar.id, calendar.working_hours, ZoneInfo("UTC"), [], start, 8 * 3600)
    # 8 business hours from Monday 09:00 lands at 17:00.
    assert deadline == datetime(2026, 1, 12, 17, 0, tzinfo=timezone.utc)


def test_work_order_has_active_sla(session, tenant_id, defaults, make_work_order):
    wo = make_work_order()
    assert wo.field_sla_status == "ACTIVE"
    assert wo.arrival_deadline is not None
    assert wo.completion_deadline is not None
    assert wo.completion_deadline > wo.arrival_deadline
    sla = sla_service.get_field_sla(session, wo)
    assert sla is not None
    assert sla.selected_reason == "global:FIELD_DEFAULT"


def test_pause_resume_accumulates_seconds(session, tenant_id, defaults, make_work_order):
    wo = make_work_order()
    sla = sla_service.get_field_sla(session, wo)
    assert pause_field_sla(session, sla, reason="customer unavailable", policy_rule="pause_on_states")
    assert sla.status == "PAUSED"
    assert sla.paused_at is not None
    assert resume_field_sla(session, sla)
    assert sla.status == "ACTIVE"
    assert sla.paused_accumulated_seconds >= 0
    assert sla.paused_at is None
    session.commit()
    assert session.query(FieldSLAPause).filter_by(sla_id=sla.id).count() >= 1


def test_evaluate_breach_and_idempotency(session, tenant_id, defaults, make_work_order):
    wo = make_work_order()
    sla = sla_service.get_field_sla(session, wo)
    far_future = TODAY + timedelta(days=3)
    first = evaluate_field_sla(session, sla, now=far_future)
    assert first["breached"] is True
    assert sla.status == "BREACHED"
    assert sla.breach_at is not None

    from app.services.audit_service import work_order_events

    events = [e for e in work_order_events(session, wo.id) if e.event_type == "work_order.sla_breached"]
    assert len(events) == 1

    # Re-evaluation (worker restart / duplicate scheduler) must not duplicate.
    second = evaluate_field_sla(session, sla, now=far_future + timedelta(hours=1))
    assert second["changed"] is False
    events2 = [e for e in work_order_events(session, wo.id) if e.event_type == "work_order.sla_breached"]
    assert len(events2) == 1


def test_evaluate_at_risk_before_breach(session, tenant_id, defaults, make_work_order):
    wo = make_work_order()
    sla = sla_service.get_field_sla(session, wo)
    # Evaluate right before the arrival deadline (within at-risk window).
    before_deadline = sla.arrival_deadline - timedelta(minutes=1)
    result = evaluate_field_sla(session, sla, now=before_deadline)
    assert result["breached"] is False
    assert sla.status in ("AT_RISK", "ACTIVE")


def test_apply_exception_overrides_deadlines(session, tenant_id, defaults, make_work_order):
    wo = make_work_order()
    new_arrival = TODAY + timedelta(days=5)
    new_completion = TODAY + timedelta(days=6)
    sla = sla_service.apply_exception(session, tenant_id, wo, arrival_deadline=new_arrival,
                                      completion_deadline=new_completion, reason="supervisor approval",
                                      actor="supervisor-1")
    assert sla.arrival_deadline == new_arrival
    assert sla.completion_deadline == new_completion
    wo.arrival_deadline = sla.arrival_deadline
    wo.completion_deadline = sla.completion_deadline


def test_select_policy_global_default(session, tenant_id, defaults):
    policy, reason = select_policy(session, tenant_id, work_order_type="NEW_INSTALLATION", priority="P3_MEDIUM")
    assert policy.code == "FIELD_DEFAULT"
    assert reason.startswith("global:")


def test_tenant_policy_preferred(session, tenant_id, defaults):
    policy = sla_service.create_policy(session, tenant_id, code="FIELD_GOLD", name="Gold SLA", actor="test")
    sla_service.create_version(session, tenant_id, policy.id,
                               definition={"pause_on_states": ["AWAITING_PARTS"], "escalation": []},
                               targets=[{"kind": "ARRIVAL", "business_seconds": 1800, "priority": "ALL"},
                                        {"kind": "TIME_TO_COMPLETE", "business_seconds": 7200, "priority": "ALL"}],
                               actor="test", activate=True)
    session.commit()
    selected, reason = select_policy(session, tenant_id, work_order_type="FAULT_REPAIR", priority="P3_MEDIUM")
    assert selected.code == "FIELD_GOLD"
    assert reason == "tenant:FIELD_GOLD"


def test_versioned_policy_activation(session, tenant_id, defaults):
    policy = sla_service.create_policy(session, tenant_id, code="FIELD_BRONZE", name="Bronze", actor="test")
    v1 = sla_service.create_version(session, tenant_id, policy.id,
                                    definition={"pause_on_states": [], "escalation": []},
                                    targets=[{"kind": "ARRIVAL", "business_seconds": 7200, "priority": "ALL"},
                                             {"kind": "TIME_TO_COMPLETE", "business_seconds": 14400, "priority": "ALL"}],
                                    actor="test", activate=True)
    session.commit()
    v2 = sla_service.create_version(session, tenant_id, policy.id,
                                    definition={"pause_on_states": [], "escalation": []},
                                    targets=[{"kind": "ARRIVAL", "business_seconds": 3600, "priority": "ALL"},
                                             {"kind": "TIME_TO_COMPLETE", "business_seconds": 10800, "priority": "ALL"}],
                                    actor="test", activate=True)
    session.commit()
    assert v1.is_active is False
    assert v2.is_active is True
    active = sla_service.active_version(session, policy)
    assert active.version == 2
