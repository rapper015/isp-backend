"""SLA engine tests: selection, versioned snapshot immutability, business
hours, holidays, timezones, pause/resume, at-risk, breach, priority-change
recalculation, reopen behaviour and idempotent evaluation."""
from datetime import datetime, timedelta, timezone

import pytest

from app.domain.sla import engine as sla_engine
from app.domain.sla.calendar import business_seconds_between, deadline_after, default_working_hours
from app.models import SLAPolicyVersion, SLATarget, TicketSLA
from app.services import sla_service, ticket_service
from app.services.audit_service import ticket_events
from app.services.catalog_service import get_or_create_calendar


from app.services.catalog_service import get_or_create_calendar


def _utc(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


_ALL_DAY = {d: [["00:00", "24:00"]] for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}


def make_tenant_policy(session, tenant_id, *, response=3600, resolution=7200, at_risk_pct=50):
    # Force a 24x7 calendar so business seconds == wall seconds (deterministic tests).
    calendar = get_or_create_calendar(session, tenant_id)
    calendar.working_hours = _ALL_DAY
    session.flush()
    policy = sla_service.create_policy(session, tenant_id, code="TENANT_SLA", name="Tenant SLA")
    version = sla_service.create_version(
        session, tenant_id, policy.id,
        definition={
            "pause_on_states": ["PENDING_CUSTOMER"],
            "reopen_policy": "RESTART",
            "reset_on_reassign": False,
            "acknowledgement_counts_as_first_response": False,
            "escalation": [
                {"target": "RESPONSE", "at_risk_pct": at_risk_pct, "level": 1, "action": "NOTIFY_AGENT"},
                {"target": "RESOLUTION", "at_risk_pct": at_risk_pct, "level": 1, "action": "NOTIFY_TEAM_LEAD"},
            ],
        },
        targets=[
            {"priority": "ALL", "kind": "RESPONSE", "business_seconds": response},
            {"priority": "ALL", "kind": "RESOLUTION", "business_seconds": resolution},
            {"priority": "P1_CRITICAL", "kind": "RESPONSE", "business_seconds": 900},
            {"priority": "P1_CRITICAL", "kind": "RESOLUTION", "business_seconds": 1800},
        ],
        activate=True)
    session.commit()
    return policy, version


@pytest.fixture
def tenant_policy(session, tenant_id):
    return make_tenant_policy(session, tenant_id)


def make_ticket_with_policy(session, tenant_id, priority="P3_MEDIUM"):
    return ticket_service.create_ticket(
        session, tenant_id, ticket_type="INQUIRY", subject="sla", description="sla test",
        customer_id="c", priority=priority, impact="LOW", urgency="LOW")
    # priority passed wins over the matrix


def test_sla_instantiation_snapshot(session, tenant_id, tenant_policy, make_ticket):
    ticket = make_ticket(priority="P3_MEDIUM")
    sla = sla_service.get_ticket_sla(session, ticket)
    assert sla is not None
    assert sla.policy_id == tenant_policy[0].id
    assert sla.policy_version == 1
    assert sla.response_target_seconds == 3600
    assert sla.resolution_target_seconds == 7200
    assert sla.status == "ACTIVE"
    assert sla.policy_snapshot["policy_code"] == "TENANT_SLA"
    assert sla.response_deadline > sla.started_at
    assert sla.resolution_deadline > sla.response_deadline
    assert ticket.response_deadline == sla.response_deadline


def test_published_version_is_immutable_snapshot(session, tenant_id, tenant_policy, make_ticket):
    policy, _ = tenant_policy
    ticket = make_ticket(priority="P3_MEDIUM")
    sla_before = sla_service.get_ticket_sla(session, ticket)
    deadline_before = sla_before.resolution_deadline

    # Publish v2 with different targets; existing ticket SLA must be untouched.
    sla_service.create_version(
        session, tenant_id, policy.id,
        definition={"pause_on_states": ["PENDING_CUSTOMER"], "reopen_policy": "RESTART", "escalation": []},
        targets=[{"priority": "ALL", "kind": "RESPONSE", "business_seconds": 60},
                 {"priority": "ALL", "kind": "RESOLUTION", "business_seconds": 120}],
        activate=True)
    session.commit()
    session.refresh(sla_before)
    assert sla_before.resolution_target_seconds == 7200
    assert sla_before.resolution_deadline == deadline_before
    assert sla_before.policy_version == 1


def test_business_seconds_between(session):
    wh = default_working_hours()
    tz = timezone.utc
    # Monday 10:00 -> Monday 12:00 = 2h (both within 9-18)
    assert business_seconds_between(session, None, wh, tz, set(), _utc(2026, 8, 31, 10), _utc(2026, 8, 31, 12)) == 7200
    # Monday 17:30 -> Tuesday 10:30 = 0.5h + 1.5h = 2h
    assert business_seconds_between(session, None, wh, tz, set(), _utc(2026, 8, 31, 17, 30), _utc(2026, 9, 1, 10, 30)) == 7200
    # Friday 17:00 -> Monday 10:00 = Fri 1h + Sat 4h + Mon 1h = 6h (Sat 10-14 works)
    assert business_seconds_between(session, None, wh, tz, set(), _utc(2026, 9, 4, 17), _utc(2026, 9, 7, 10)) == 21600
    # Reversed range is zero
    assert business_seconds_between(session, None, wh, tz, set(), _utc(2026, 8, 31, 12), _utc(2026, 8, 31, 10)) == 0


def test_deadline_after_working_hours(session):
    wh = default_working_hours()
    tz = timezone.utc
    # Monday 10:00 + 3600s -> 11:00
    assert deadline_after(session, None, wh, tz, set(), _utc(2026, 8, 31, 10), 3600) == _utc(2026, 8, 31, 11)
    # Monday 17:30 + 2h -> Tuesday 10:30
    assert deadline_after(session, None, wh, tz, set(), _utc(2026, 8, 31, 17, 30), 7200) == _utc(2026, 9, 1, 10, 30)
    # Friday 17:00 + 1h -> Friday 18:00 (same working day)
    assert deadline_after(session, None, wh, tz, set(), _utc(2026, 9, 4, 17), 3600) == _utc(2026, 9, 4, 18)


def test_deadline_after_holiday(session, tenant_id, defaults):
    from app.models import Holiday

    calendar = get_or_create_calendar(session, tenant_id)
    # Tuesday 2026-09-01 is a holiday.
    session.add(Holiday(tenant_id=tenant_id, calendar_id=calendar.id, holiday_date=datetime(2026, 9, 1).date(), name="test"))
    session.commit()
    tz = timezone.utc
    # Monday 17:00 + 2h skips Tuesday (holiday) -> Wednesday 10:00
    deadline = deadline_after(session, calendar.id, default_working_hours(), tz,
                              {datetime(2026, 9, 1).date()}, _utc(2026, 8, 31, 17), 7200)
    assert deadline == _utc(2026, 9, 2, 10)


def test_timezone_awareness(session):
    from zoneinfo import ZoneInfo

    wh = default_working_hours()
    tz = ZoneInfo("Asia/Kolkata")
    start = _utc(2026, 8, 31, 5, 30)  # 11:00 IST Monday
    end = _utc(2026, 8, 31, 7, 30)  # 13:00 IST Monday
    assert business_seconds_between(session, None, wh, tz, set(), start, end) == 7200


def test_pause_and_resume_extend_deadline(session, tenant_id, tenant_policy, make_ticket):
    ticket = make_ticket(priority="P3_MEDIUM")
    sla = sla_service.get_ticket_sla(session, ticket)
    deadline_before = sla.resolution_deadline
    ticket_service.request_customer_info(session, tenant_id, ticket.id, message="need more info")
    session.commit()
    assert sla.status == "PAUSED"
    assert sla.paused_at is not None
    # Resume after 1h of wall time (which is also business time on a weekday).
    sla.paused_at = sla.paused_at - timedelta(hours=1)
    ticket_service.accept(session, tenant_id, ticket.id)
    session.commit()
    assert sla.status == "ACTIVE"
    assert sla.paused_at is None
    assert sla.paused_accumulated_seconds == 3600
    assert sla.resolution_deadline > deadline_before
    events = [e.event_type for e in ticket_events(session, ticket.id)]
    assert "ticket.sla_paused" in events
    assert "ticket.sla_resumed" in events


def test_at_risk_detection_idempotent(session, tenant_id, tenant_policy, make_ticket):
    ticket = make_ticket(priority="P3_MEDIUM")
    # First human response already sent -> only the resolution deadline is evaluated.
    from app.services import communication_service

    communication_service.add_comment(session, tenant_id, ticket, kind="PUBLIC_REPLY", body="investigating",
                                      sender_type="AGENT", sender_id="a1")
    session.commit()
    sla = sla_service.get_ticket_sla(session, ticket)
    # Advance so the resolution deadline has ~3000s of 7200s remaining (<= 50% threshold).
    future = sla.resolution_deadline - timedelta(seconds=3000)
    result = sla_engine.evaluate_sla(session, sla, now=future)
    assert result["at_risk"] is True
    assert sla.status == "AT_RISK"
    assert sla.at_risk_at is not None
    events = [e.event_type for e in ticket_events(session, ticket.id)]
    assert events.count("ticket.sla_at_risk") == 1
    # Second evaluation does not duplicate the event.
    sla_engine.evaluate_sla(session, sla, now=future)
    session.flush()
    events2 = [e.event_type for e in ticket_events(session, ticket.id)]
    assert events2.count("ticket.sla_at_risk") == 1


def test_breach_detection(session, tenant_id, tenant_policy, make_ticket):
    ticket = make_ticket(priority="P3_MEDIUM")
    sla = sla_service.get_ticket_sla(session, ticket)
    future = sla.resolution_deadline + timedelta(seconds=60)
    result = sla_engine.evaluate_sla(session, sla, now=future)
    assert result["breached"] is True
    assert sla.status == "BREACHED"
    assert sla.breach_at is not None
    events = [e.event_type for e in ticket_events(session, ticket.id)]
    assert "ticket.sla_breached" in events
    # Idempotent: no duplicate breach events.
    sla_engine.evaluate_sla(session, sla, now=future + timedelta(hours=1))
    session.flush()
    events2 = [e.event_type for e in ticket_events(session, ticket.id)]
    assert events2.count("ticket.sla_breached") == 1


def test_paused_sla_does_not_advance(session, tenant_id, tenant_policy, make_ticket):
    ticket = make_ticket(priority="P3_MEDIUM")
    sla = sla_service.get_ticket_sla(session, ticket)
    ticket_service.request_customer_info(session, tenant_id, ticket.id, message="waiting")
    session.commit()
    assert sla.status == "PAUSED"
    future = sla.resolution_deadline + timedelta(days=2)
    result = sla_engine.evaluate_sla(session, sla, now=future)
    assert result["breached"] is False
    assert sla.status == "PAUSED"


def test_priority_change_recalculates_sla(session, tenant_id, tenant_policy, make_ticket):
    ticket = make_ticket(priority="P3_MEDIUM")
    sla = sla_service.get_ticket_sla(session, ticket)
    assert sla.response_target_seconds == 3600
    ticket_service.change_priority(session, tenant_id, ticket.id, priority="P1_CRITICAL", reason="major incident")
    session.commit()
    assert sla.response_target_seconds == 900  # P1 specific target
    assert sla.resolution_target_seconds == 1800
    assert sla.status == "ACTIVE"


def test_reopen_restarts_sla_run(session, tenant_id, tenant_policy, make_ticket):
    ticket = make_ticket(priority="P3_MEDIUM")
    sla = sla_service.get_ticket_sla(session, ticket)
    original_start = sla.started_at
    ticket_service.resolve(session, tenant_id, ticket.id, resolution_code="NO_FAULT_FOUND", summary="ok")
    session.commit()
    ticket_service.close(session, tenant_id, ticket.id)
    session.commit()
    ticket_service.reopen(session, tenant_id, ticket.id, reason="still down")
    session.commit()
    assert sla.status == "ACTIVE"
    assert sla.started_at >= original_start
    # Full targets again.
    assert sla.response_target_seconds == 3600
    assert sla.resolution_target_seconds == 7200


def test_sla_override_audited(session, tenant_id, tenant_policy, make_ticket):
    ticket = make_ticket(priority="P3_MEDIUM")
    sla = sla_service.get_ticket_sla(session, ticket)
    new_deadline = sla.resolution_deadline + timedelta(days=2)
    sla_service.apply_sla_override(session, tenant_id, ticket, response_deadline=sla.response_deadline + timedelta(days=2),
                                   resolution_deadline=new_deadline, reason="network team confirmed ETA", actor="sup-1")
    session.commit()
    assert sla.resolution_deadline == new_deadline
    events = [e.event_type for e in ticket_events(session, ticket.id)]
    assert "ticket.sla_override" in events


def test_reconcile_sla_invariant(session, tenant_id, tenant_policy, make_ticket):
    ticket = make_ticket(priority="P3_MEDIUM")
    sla = sla_service.get_ticket_sla(session, ticket)
    # Corrupt the deadline, then reconcile repairs it to the invariant.
    sla.resolution_deadline = sla.resolution_deadline + timedelta(days=10)
    result = sla_engine.reconcile_sla(session, sla)
    assert result["resolution_deadline"] != sla.resolution_deadline.isoformat() or sla.resolution_deadline < datetime.now(timezone.utc) + timedelta(days=5)


def test_resolution_seconds_after_assign_not_reset(session, tenant_id, tenant_policy, make_ticket):
    ticket = make_ticket(priority="P3_MEDIUM")
    sla = sla_service.get_ticket_sla(session, ticket)
    deadline_before = sla.resolution_deadline
    ticket_service.assign(session, tenant_id, ticket.id, agent_id="a1")
    session.commit()
    assert sla.resolution_deadline == deadline_before  # reassignment must not reset SLA
