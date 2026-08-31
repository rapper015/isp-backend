"""Commission engine: versioned rules, deterministic earnings, clawbacks,
adjustments, refund clawbacks and reproducibility."""
import uuid
from decimal import Decimal

import pytest

from app.domain import commissions as commission_rules
from app.domain.exceptions import CommissionError, DuplicateError, UnsafeRuleError, ValidationError
from app.services import commission_service


def test_fixed_and_percentage_rules():
    result = commission_rules.calculate({"calculation_type": "FIXED_AMOUNT", "fixed_amount": 500},
                                        basis_amount=Decimal("1000"))
    assert result["amount"] == Decimal("500")
    result = commission_rules.calculate({"calculation_type": "PERCENTAGE", "rate": 10},
                                        basis_amount=Decimal("1000"))
    assert result["amount"] == Decimal("100")


def test_tiered_and_slab_rules():
    tiers = [{"min": 0, "max": 5000, "rate": 5}, {"min": 5000, "max": None, "rate": 10}]
    result = commission_rules.calculate({"calculation_type": "TIERED_PERCENTAGE", "tiers": tiers},
                                        basis_amount=Decimal("10000"))
    # Banded tiered: 10% (highest applicable tier) applied to the full basis.
    assert result["amount"] == Decimal("1000")
    slabs = [{"min": 0, "max": 1000, "amount": 50}, {"min": 1000, "max": None, "amount": 200}]
    result = commission_rules.calculate({"calculation_type": "SLAB", "slabs": slabs},
                                        basis_amount=Decimal("2000"))
    assert result["amount"] == Decimal("200")


def test_unsafe_formula_rejected(session, tenant):
    from app.services import commission_service as cs

    plan = cs.create_plan(session, tenant.id, code="UNSAFE", name="Unsafe")
    session.commit()
    with pytest.raises(ValidationError):
        cs.add_rule(session, tenant.id, plan.id, code="R1", name="r", basis="PAYMENT_COLLECTION",
                    calculation_type="eval()", rate=1)
    with pytest.raises(ValidationError):
        cs.add_rule(session, tenant.id, plan.id, code="R2", name="r", basis="PAYMENT_COLLECTION",
                    calculation_type="PERCENTAGE", rate=150)  # >100 invalid


def test_recognize_earning_idempotent(session, tenant, make_partner, make_commission_plan):
    partner = make_partner()
    plan, rule = make_commission_plan()
    from app.services import commission_service as cs

    cs.create_agreement(session, tenant.id, partner_id=partner.id, plan_id=plan.id)
    session.commit()
    earning = cs.recognize_earning(session, tenant.id, partner_id=partner.id,
                                   source_event_id="evt-1", source_event_type="billing.payment.captured.v1",
                                   basis="PAYMENT_COLLECTION", basis_amount=1000, currency="INR")
    session.commit()
    assert earning.amount == 100.0  # 10% of 1000
    assert "10.0%" in earning.rate_formula
    # Duplicate source event -> same earning, not duplicated.
    again = cs.recognize_earning(session, tenant.id, partner_id=partner.id,
                                 source_event_id="evt-1", source_event_type="billing.payment.captured.v1",
                                 basis="PAYMENT_COLLECTION", basis_amount=1000, currency="INR")
    session.commit()
    assert again.id == earning.id
    from app.models import CommissionEarning

    count = session.scalars(__import__("sqlalchemy").select(
        __import__("sqlalchemy").func.count()).select_from(CommissionEarning).where(
        CommissionEarning.source_event_id == "evt-1")).one()
    assert count == 1


def test_clawback_never_deletes_earning(session, tenant, make_partner, make_commission_plan):
    partner = make_partner()
    plan, rule = make_commission_plan()
    from app.services import commission_service as cs

    cs.create_agreement(session, tenant.id, partner_id=partner.id, plan_id=plan.id)
    session.commit()
    earning = cs.recognize_earning(session, tenant.id, partner_id=partner.id,
                                   source_event_id="evt-2", source_event_type="billing.payment.captured.v1",
                                   basis="PAYMENT_COLLECTION", basis_amount=1000)
    session.commit()
    clawback = cs.clawback_earning(session, tenant.id, earning.id, amount=None, kind="REFUND",
                                   source_event_id="refund-1", reason="customer refund")
    session.commit()
    assert clawback.amount == 100.0
    from app.models import CommissionEarning

    still_there = session.get(CommissionEarning, earning.id)
    assert still_there is not None and still_there.status == "CLAWED_BACK"
    # Idempotent clawback.
    again = cs.clawback_earning(session, tenant.id, earning.id, amount=None, kind="REFUND",
                                source_event_id="refund-1", reason="customer refund")
    assert again.id == clawback.id


def test_adjustment(session, tenant, make_partner, make_commission_plan):
    partner = make_partner()
    plan, rule = make_commission_plan()
    from app.services import commission_service as cs

    cs.create_agreement(session, tenant.id, partner_id=partner.id, plan_id=plan.id)
    session.commit()
    earning = cs.recognize_earning(session, tenant.id, partner_id=partner.id,
                                   source_event_id="evt-3", source_event_type="billing.payment.captured.v1",
                                   basis="PAYMENT_COLLECTION", basis_amount=1000)
    session.commit()
    adjustment = cs.adjust_earning(session, tenant.id, earning.id, amount=-20, kind="MANUAL_CORRECTION",
                                   reason="fix", actor="finance")
    assert adjustment.amount == -20


def test_no_agreement_blocks_earning(session, tenant, make_partner, make_commission_plan):
    partner = make_partner()
    with pytest.raises(CommissionError):
        commission_service.recognize_earning(session, tenant.id, partner_id=partner.id,
                                             source_event_id="evt-4",
                                             source_event_type="billing.payment.captured.v1",
                                             basis="PAYMENT_COLLECTION", basis_amount=1000)
