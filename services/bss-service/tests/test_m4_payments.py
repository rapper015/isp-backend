"""M4 payment flow: server-side amount, idempotent capture, allocation,
overpayment credit, derived invoice status, receipt, ledger."""
import uuid
from decimal import Decimal

import pytest

from app.revenue.models import (
    InvoiceLineItem,
    LedgerBalanceProjection,
    PaymentAllocation,
    PaymentIntent,
    PaymentTransaction,
    Receipt,
    RevenueInvoice,
)
from app.revenue.payments import (
    capture_payment,
    create_payment_intent,
    server_side_payable,
    start_hosted_checkout,
)
from app.revenue.money import money


def _idem(prefix="k"):
    return f"{prefix}-{uuid.uuid4().hex}"


def test_intent_amount_is_server_side(session, tenant, account, invoice):
    intent = create_payment_intent(session, tenant.id, billing_account_id=account.id, idempotency_key=_idem(), correlation_id="c1", invoice_ids=[invoice.id])
    session.commit()
    assert intent.amount == Decimal("1000.00")
    assert intent.status == "CREATED"


def test_intent_rejects_frontend_amount_above_payable(session, tenant, account, invoice):
    with pytest.raises(ValueError):
        create_payment_intent(session, tenant.id, billing_account_id=account.id, amount=Decimal("99999.00"), idempotency_key=_idem(), correlation_id="c1", invoice_ids=[invoice.id])


def test_intent_allow_overpayment(session, tenant, account, invoice):
    intent = create_payment_intent(session, tenant.id, billing_account_id=account.id, amount=Decimal("1500.00"), allow_overpayment=True, idempotency_key=_idem(), correlation_id="c1", invoice_ids=[invoice.id])
    session.commit()
    assert intent.amount == Decimal("1500.00")


def test_start_checkout_returns_safe_payload(session, tenant, account, gateway, invoice):
    intent = create_payment_intent(session, tenant.id, billing_account_id=account.id, gateway_account_id=gateway.id, idempotency_key=_idem(), correlation_id="c1", invoice_ids=[invoice.id])
    session.commit()
    intent, attempt, safe_payload = start_hosted_checkout(session, tenant.id, intent.id)
    session.commit()
    assert intent.status == "PENDING"
    assert attempt.status == "SUBMITTED"
    assert intent.gateway_order_ref
    assert "checkout_url" in safe_payload
    assert "secret" not in str(safe_payload).lower()


def test_capture_is_idempotent(session, tenant, account, invoice):
    intent = create_payment_intent(session, tenant.id, billing_account_id=account.id, idempotency_key=_idem(), correlation_id="c1", invoice_ids=[invoice.id])
    session.commit()
    idem = _idem("capture")
    txn1 = capture_payment(session, tenant.id, intent_id=intent.id, external_ref="ext-1", amount=Decimal("1000.00"), currency="INR", idempotency_key=idem, correlation_id="c1")
    session.commit()
    txn2 = capture_payment(session, tenant.id, intent_id=intent.id, external_ref="ext-1", amount=Decimal("1000.00"), currency="INR", idempotency_key=idem, correlation_id="c1")
    session.commit()
    assert txn1.id == txn2.id
    assert session.query(PaymentTransaction).filter(PaymentTransaction.tenant_id == tenant.id).count() == 1  # duplicate capture = one transaction


def test_duplicate_external_ref_different_idem_rejected(session, tenant, account, invoice):
    intent = create_payment_intent(session, tenant.id, billing_account_id=account.id, idempotency_key=_idem(), correlation_id="c1", invoice_ids=[invoice.id])
    session.commit()
    capture_payment(session, tenant.id, intent_id=intent.id, external_ref="ext-X", amount=Decimal("1000.00"), currency="INR", idempotency_key=_idem(), correlation_id="c1")
    session.commit()
    with pytest.raises(ValueError):
        capture_payment(session, tenant.id, intent_id=intent.id, external_ref="ext-X", amount=Decimal("1000.00"), currency="INR", idempotency_key=_idem(), correlation_id="c1")


def test_full_payment_marks_invoice_paid_and_posts_ledger(session, tenant, account, invoice):
    intent = create_payment_intent(session, tenant.id, billing_account_id=account.id, idempotency_key=_idem(), correlation_id="c1", invoice_ids=[invoice.id])
    session.commit()
    txn = capture_payment(session, tenant.id, intent_id=intent.id, external_ref="ext-full", amount=Decimal("1000.00"), currency="INR", idempotency_key=_idem(), correlation_id="c1")
    session.commit()
    invoice = session.get(RevenueInvoice, invoice.id)
    assert invoice.status == "PAID"
    assert invoice.paid_amount == Decimal("1000.00")
    allocations = session.query(PaymentAllocation).filter(PaymentAllocation.transaction_id == txn.id).all()
    assert sum(a.amount for a in allocations) == Decimal("1000.00")
    receipt = session.query(Receipt).filter(Receipt.transaction_id == txn.id).one()
    assert receipt.amount == Decimal("1000.00")
    # Balanced ledger projection exists.
    assert session.query(LedgerBalanceProjection).filter(LedgerBalanceProjection.tenant_id == tenant.id).count() >= 1


def test_partial_payment_updates_balance(session, tenant, account, invoice):
    intent = create_payment_intent(session, tenant.id, billing_account_id=account.id, idempotency_key=_idem(), correlation_id="c1", invoice_ids=[invoice.id])
    session.commit()
    capture_payment(session, tenant.id, intent_id=intent.id, external_ref="ext-part", amount=Decimal("400.00"), currency="INR", idempotency_key=_idem(), correlation_id="c1")
    session.commit()
    invoice = session.get(RevenueInvoice, invoice.id)
    assert invoice.status == "PARTIALLY_PAID"
    assert invoice.paid_amount == Decimal("400.00")
    assert server_side_payable(session, tenant.id, account) == Decimal("600.00")


def test_multiple_payments_one_invoice(session, tenant, account, invoice):
    intent1 = create_payment_intent(session, tenant.id, billing_account_id=account.id, idempotency_key=_idem(), correlation_id="c1", invoice_ids=[invoice.id])
    session.commit()
    capture_payment(session, tenant.id, intent_id=intent1.id, external_ref="p1", amount=Decimal("600.00"), currency="INR", idempotency_key=_idem(), correlation_id="c1")
    session.commit()
    intent2 = create_payment_intent(session, tenant.id, billing_account_id=account.id, idempotency_key=_idem(), correlation_id="c1", invoice_ids=[invoice.id])
    session.commit()
    capture_payment(session, tenant.id, intent_id=intent2.id, external_ref="p2", amount=Decimal("400.00"), currency="INR", idempotency_key=_idem(), correlation_id="c1")
    session.commit()
    invoice = session.get(RevenueInvoice, invoice.id)
    assert invoice.status == "PAID"
    assert invoice.paid_amount == Decimal("1000.00")


def test_overpayment_creates_credit(session, tenant, account, invoice):
    intent = create_payment_intent(session, tenant.id, billing_account_id=account.id, amount=Decimal("1200.00"), allow_overpayment=True, idempotency_key=_idem(), correlation_id="c1", invoice_ids=[invoice.id])
    session.commit()
    capture_payment(session, tenant.id, intent_id=intent.id, external_ref="ext-over", amount=Decimal("1200.00"), currency="INR", idempotency_key=_idem(), correlation_id="c1")
    session.commit()
    account = session.get(type(account), account.id)
    assert account.credit_balance == Decimal("200.00")
    invoice = session.get(RevenueInvoice, invoice.id)
    assert invoice.status == "PAID"


def test_oldest_invoice_first_allocation(session, tenant, account):
    from datetime import datetime, timedelta, timezone

    def _inv(amount, days):
        item = RevenueInvoice(
            tenant_id=tenant.id,
            billing_account_id=account.id,
            invoice_number=f"INV-{uuid.uuid4().hex[:8].upper()}",
            currency="INR",
            total_amount=Decimal(amount),
            paid_amount=Decimal("0.00"),
            written_off_amount=Decimal("0.00"),
            status="ISSUED",
            issued_at=datetime.now(timezone.utc),
            due_date=datetime.now(timezone.utc) + timedelta(days=days),
        )
        session.add(item)
        session.commit()
        return item

    old = _inv("800.00", 5)
    new = _inv("600.00", 40)
    intent = create_payment_intent(session, tenant.id, billing_account_id=account.id, amount=Decimal("1000.00"), allow_overpayment=True, idempotency_key=_idem(), correlation_id="c1")
    session.commit()
    txn = capture_payment(session, tenant.id, intent_id=intent.id, external_ref="ext-old", amount=Decimal("1000.00"), currency="INR", idempotency_key=_idem(), correlation_id="c1")
    session.commit()
    allocations = session.query(PaymentAllocation).filter(PaymentAllocation.transaction_id == txn.id).all()
    by_invoice = {str(a.invoice_id): a.amount for a in allocations}
    assert by_invoice[str(old.id)] == Decimal("800.00")
    assert by_invoice[str(new.id)] == Decimal("200.00")
    assert session.get(RevenueInvoice, old.id).status == "PAID"
    assert session.get(RevenueInvoice, new.id).status == "PARTIALLY_PAID"
