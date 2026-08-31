"""Inbound event consumers: billing payment -> earning, refund -> clawback,
duplicate dedup, wrong-tenant rejection, missing-tenant rejection."""
import uuid

from app.context import get_context
from app.messaging.consumers import handle_event
from app.models import CommissionEarning
from app.services import commission_service, organization_service


def _event(event_type, tenant_id, payload, event_id=None):
    return {"id": event_id or str(uuid.uuid4()), "event_type": event_type,
            "tenant_id": str(tenant_id), "payload": payload}


def _active_partner(session, tenant):
    partner = organization_service.create_partner(session, tenant.id, partner_type="FRANCHISE",
                                                  code=f"FR-{uuid.uuid4().hex[:6]}", name="Franchise")
    session.commit()
    organization_service.change_partner_status(session, tenant.id, partner.id, to_status="ONBOARDING",
                                               reason="onboard")
    organization_service.change_partner_status(session, tenant.id, partner.id, to_status="ACTIVE",
                                               reason="onboarded")
    session.commit()
    return partner


def test_payment_captured_creates_earning(session, tenant, make_commission_plan):
    partner = _active_partner(session, tenant)
    plan, rule = make_commission_plan()
    commission_service.create_agreement(session, tenant.id, partner_id=partner.id, plan_id=plan.id)
    session.commit()
    result = handle_event(session, _event("billing.payment.captured.v1", tenant.id, {
        "partner_id": str(partner.id), "customer_id": "C-1", "amount": 1000, "currency": "INR",
        "payment_ref": "PAY-1"}))
    assert result["handled"] is True
    rows = list(session.scalars(__import__("sqlalchemy").select(CommissionEarning).where(
        CommissionEarning.tenant_id == tenant.id)))
    assert len(rows) == 1 and rows[0].amount == 100.0


def test_payment_refund_creates_clawback(session, tenant, make_commission_plan):
    partner = _active_partner(session, tenant)
    plan, rule = make_commission_plan()
    commission_service.create_agreement(session, tenant.id, partner_id=partner.id, plan_id=plan.id)
    session.commit()
    handle_event(session, _event("billing.payment.captured.v1", tenant.id, {
        "partner_id": str(partner.id), "amount": 1000, "payment_ref": "PAY-2"}))
    result = handle_event(session, _event("billing.payment.refunded.v1", tenant.id, {
        "partner_id": str(partner.id), "payment_ref": "PAY-2"}))
    assert result["handled"] is True and "clawback" in result["action"]
    from app.models import CommissionClawback

    clawbacks = list(session.scalars(__import__("sqlalchemy").select(CommissionClawback).where(
        CommissionClawback.tenant_id == tenant.id)))
    assert len(clawbacks) == 1


def test_duplicate_event_deduped(session, tenant, make_commission_plan):
    partner = _active_partner(session, tenant)
    plan, rule = make_commission_plan()
    commission_service.create_agreement(session, tenant.id, partner_id=partner.id, plan_id=plan.id)
    session.commit()
    event = _event("billing.payment.captured.v1", tenant.id, {
        "partner_id": str(partner.id), "amount": 1000}, event_id="dup-1")
    first = handle_event(session, event)
    second = handle_event(session, event)
    assert first["handled"] is True
    assert second["handled"] is False and second["action"] == "duplicate"


def test_unknown_event_ignored(session, tenant):
    result = handle_event(session, _event("nms.signal.v1", tenant.id, {}))
    assert result["handled"] is True and result["action"] == "ignored"


def test_missing_tenant_rejected(session, tenant, make_commission_plan):
    event = {"id": str(uuid.uuid4()), "event_type": "billing.payment.captured.v1",
             "tenant_id": None, "payload": {"amount": 100}}
    result = handle_event(session, event)
    assert result["handled"] is False and result["action"] == "missing_tenant"


def test_wrong_tenant_entity_isolated(session, tenant, tenant_b):
    # Tenant A's event for an entity owned by Tenant B is not applied.
    partner_b = organization_service.create_partner(session, tenant_b.id, partner_type="FRANCHISE",
                                                    code=f"FRB-{uuid.uuid4().hex[:6]}", name="B")
    session.commit()
    result = handle_event(session, _event("billing.payment.captured.v1", tenant.id, {
        "partner_id": str(partner_b.id), "amount": 100}))
    # partner not found for tenant A -> earning:no_partner, no cross-tenant write.
    assert result["handled"] is True
    assert session.scalars(__import__("sqlalchemy").select(__import__("sqlalchemy").func.count()).select_from(
        CommissionEarning).where(CommissionEarning.tenant_id == tenant.id)).one() == 0


def test_context_cleared_after_processing(session, tenant, make_commission_plan):
    partner = _active_partner(session, tenant)
    plan, rule = make_commission_plan()
    commission_service.create_agreement(session, tenant.id, partner_id=partner.id, plan_id=plan.id)
    session.commit()
    handle_event(session, _event("billing.payment.captured.v1", tenant.id, {
        "partner_id": str(partner.id), "amount": 100}))
    assert get_context() is None  # no ambient leakage
