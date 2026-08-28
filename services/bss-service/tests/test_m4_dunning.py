"""M4 dunning: versioned policies, stage escalation, suspension events, holds."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.revenue.dunning import (
    add_dunning_stage,
    advance_dunning_case,
    create_dunning_policy,
    open_dunning_case,
    pause_dunning_case,
    place_collection_hold,
    publish_dunning_policy,
    record_promise_to_pay,
    resume_dunning_case,
    resolve_dunning_case,
)
from app.revenue.models import DunningCase, OutboxEvent, RevenueInvoice


def _overdue_invoice(session, tenant, account, amount="1000.00"):
    invoice = RevenueInvoice(
        tenant_id=tenant.id,
        billing_account_id=account.id,
        invoice_number=f"INV-{__import__('uuid').uuid4().hex[:8].upper()}",
        currency="INR",
        total_amount=Decimal(amount),
        paid_amount=Decimal("0.00"),
        written_off_amount=Decimal("0.00"),
        status="ISSUED",
        issued_at=datetime.now(timezone.utc) - timedelta(days=40),
        due_date=datetime.now(timezone.utc) - timedelta(days=10),
    )
    session.add(invoice)
    session.commit()
    return invoice


def _policy(session, tenant):
    policy = create_dunning_policy(session, tenant.id, code="dun-1", name="Standard", params={"minimum_overdue": "100.00"})
    session.commit()
    version_id = policy.current_version_id
    add_dunning_stage(session, tenant.id, version_id, stage_order=1, stage_code="GRACE_PERIOD", delay_seconds=86400, action_type="NOTIFY", message_template="payment overdue")
    add_dunning_stage(session, tenant.id, version_id, stage_order=2, stage_code="RESTRICTED_SERVICE_WARNING", delay_seconds=86400, action_type="NOTIFY", message_template="service will be suspended")
    add_dunning_stage(session, tenant.id, version_id, stage_order=3, stage_code="SUSPENSION_SCHEDULED", delay_seconds=86400, action_type="SUSPEND", message_template="service suspended")
    session.commit()
    publish_dunning_policy(session, tenant.id, policy.id)
    session.commit()
    return policy, version_id


def test_dunning_case_escalates_and_publishes_suspension(session, tenant, account):
    _overdue_invoice(session, tenant, account)
    policy, version_id = _policy(session, tenant)
    case = open_dunning_case(session, tenant.id, account.id, version_id, correlation_id="c1")
    session.commit()
    assert case.status == "OPEN"
    # No explicit date control: temporarily set next_due_at to force advancement.
    case = session.get(DunningCase, case.id)
    case.next_due_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    session.commit()
    advance_dunning_case(session, tenant.id, case.id, correlation_id="c2")
    session.commit()
    case = session.get(DunningCase, case.id)
    assert case.current_stage_order == 1
    # Force all stages through to suspension.
    for _ in range(3):
        case = session.get(DunningCase, case.id)
        if case.status != "OPEN":
            break
        case.next_due_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.commit()
        advance_dunning_case(session, tenant.id, case.id, correlation_id="c3")
        session.commit()
    events = [e.event_type for e in session.query(OutboxEvent).filter(OutboxEvent.tenant_id == tenant.id).all()]
    assert "billing.account_delinquent.v1" in events
    assert "billing.suspension_required.v1" in events
    assert "dunning.stage_changed.v1" in events


def test_dunning_pause_resume_hold_promise(session, tenant, account):
    _overdue_invoice(session, tenant, account)
    policy, version_id = _policy(session, tenant)
    case = open_dunning_case(session, tenant.id, account.id, version_id, correlation_id="c1")
    session.commit()
    pause_dunning_case(session, tenant.id, case.id, actor="manager")
    session.commit()
    assert session.get(DunningCase, case.id).status == "PAUSED"
    resume_dunning_case(session, tenant.id, case.id, actor="manager")
    session.commit()
    assert session.get(DunningCase, case.id).status == "OPEN"
    hold = place_collection_hold(session, tenant.id, account.id, kind="LEGAL", reason="legal hold", created_by="manager")
    session.commit()
    assert hold.status == "ACTIVE"
    promise = record_promise_to_pay(session, tenant.id, account.id, amount=Decimal("500.00"), currency="INR", promise_date=datetime.now(timezone.utc) + timedelta(days=2), created_by="agent")
    session.commit()
    assert promise.status == "ACTIVE"
    resolve_dunning_case(session, tenant.id, case.id, correlation_id="c4")
    session.commit()
    assert session.get(DunningCase, case.id).status == "RESOLVED"


def test_dunning_does_not_run_when_not_due(session, tenant, account):
    _overdue_invoice(session, tenant, account)
    policy, version_id = _policy(session, tenant)
    case = open_dunning_case(session, tenant.id, account.id, version_id, correlation_id="c1")
    session.commit()
    # next_due_at is set to the future, so the stage does not run yet.
    case = session.get(DunningCase, case.id)
    case.next_due_at = datetime.now(timezone.utc) + timedelta(hours=1)
    session.commit()
    before = session.get(DunningCase, case.id).current_stage_order
    advance_dunning_case(session, tenant.id, case.id, correlation_id="c2")
    session.commit()
    assert session.get(DunningCase, case.id).current_stage_order == before
