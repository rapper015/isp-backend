"""M4 refunds and chargebacks."""
import uuid
from decimal import Decimal

import pytest

from app.revenue.models import Dispute, PaymentAllocation, Refund, RevenueInvoice
from app.revenue.payments import capture_payment, create_payment_intent
from app.revenue.refunds import create_chargeback, create_refund, refundable_amount


def _capture(session, tenant, account, invoice, amount="1000.00", ext=None):
    intent = create_payment_intent(session, tenant.id, billing_account_id=account.id, idempotency_key=f"r-{uuid.uuid4().hex}", correlation_id="c1", invoice_ids=[invoice.id])
    session.commit()
    return capture_payment(session, tenant.id, intent_id=intent.id, external_ref=ext or f"ext-{uuid.uuid4().hex[:8]}", amount=Decimal(amount), currency="INR", idempotency_key=f"cap-{uuid.uuid4().hex}", correlation_id="c1")


def test_refund_exceeds_captured_rejected(session, tenant, account, invoice):
    txn = _capture(session, tenant, account, invoice)
    session.commit()
    with pytest.raises(ValueError):
        create_refund(session, tenant.id, transaction_id=txn.id, amount=Decimal("1500.00"), currency="INR", reason="test", refund_reference=f"rf-{uuid.uuid4().hex}", correlation_id="c1")


def test_partial_refunds_cannot_exceed_refundable(session, tenant, account, invoice):
    txn = _capture(session, tenant, account, invoice)
    session.commit()
    create_refund(session, tenant.id, transaction_id=txn.id, amount=Decimal("600.00"), currency="INR", reason="partial", refund_reference=f"rf-{uuid.uuid4().hex}", correlation_id="c1")
    session.commit()
    assert refundable_amount(session, tenant.id, txn.id) == Decimal("400.00")
    with pytest.raises(ValueError):
        create_refund(session, tenant.id, transaction_id=txn.id, amount=Decimal("500.00"), currency="INR", reason="over", refund_reference=f"rf-{uuid.uuid4().hex}", correlation_id="c1")


def test_refund_reverses_allocation(session, tenant, account, invoice):
    txn = _capture(session, tenant, account, invoice)
    session.commit()
    refund = create_refund(session, tenant.id, transaction_id=txn.id, amount=Decimal("400.00"), currency="INR", reason="refund", refund_reference=f"rf-{uuid.uuid4().hex}", correlation_id="c1", approved_by="tester")
    session.commit()
    assert refund.status == "COMPLETED"
    invoice = session.get(RevenueInvoice, invoice.id)
    assert invoice.paid_amount == Decimal("600.00")
    assert invoice.status == "PARTIALLY_PAID"
    allocations = session.query(PaymentAllocation).filter(PaymentAllocation.transaction_id == txn.id).all()
    net = sum(a.amount for a in allocations if a.reversal_of is None) - sum(abs(a.amount) for a in allocations if a.reversal_of is not None)
    assert net == Decimal("600.00")


def test_chargeback_preserves_original_payment(session, tenant, account, gateway, invoice):
    txn = _capture(session, tenant, account, invoice)
    session.commit()
    dispute = create_chargeback(
        session,
        tenant.id,
        transaction_id=txn.id,
        gateway_dispute_ref="ds-1",
        amount=Decimal("1000.00"),
        currency="INR",
        reason="fraud",
        evidence_deadline=None,
        correlation_id="c1",
    )
    session.commit()
    assert dispute.status == "OPEN"
    assert session.get(type(txn), txn.id).status == "CONFIRMED"  # original payment intact
    assert "CHARGEBACK" in (session.get(type(account), account.id).holds or [])
    assert session.query(Dispute).filter(Dispute.tenant_id == tenant.id).count() == 1
