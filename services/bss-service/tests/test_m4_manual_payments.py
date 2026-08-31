"""M4 manual/offline payments with maker-checker."""
import uuid
from decimal import Decimal

import pytest

from app.revenue.manual_payments import (
    approve_manual_payment,
    create_manual_payment,
    post_manual_payment,
    reject_manual_payment,
    reverse_manual_payment,
    submit_manual_payment,
)
from app.revenue.models import ManualPayment, PaymentTransaction, RevenueInvoice


def _manual(session, tenant, account, amount="100.00", method="NEFT"):
    return create_manual_payment(
        session,
        tenant.id,
        billing_account_id=account.id,
        method=method,
        amount=Decimal(amount),
        currency="INR",
        external_reference="UTR-123",
        payment_date=None,
        collector="collector-a",
        branch_reference="branch-1",
        evidence={"attachment": "neft-slip.png"},
        notes="customer provided slip",
        reference_number=f"MP-{uuid.uuid4().hex[:8].upper()}",
        correlation_id="c1",
    )


def test_low_value_manual_payment_needs_no_approval(session, tenant, account, invoice):
    item = _manual(session, tenant, account, amount="100.00")
    session.commit()
    assert item.requires_approval is False  # below threshold
    submit_manual_payment(session, tenant.id, item.id, submitted_by="operator")
    session.commit()
    # No approval needed -> submit leaves it SUBMITTED; posting requires APPROVED.
    assert session.get(ManualPayment, item.id).status == "SUBMITTED"
    with pytest.raises(ValueError):
        post_manual_payment(session, tenant.id, item.id, correlation_id="c1")


def test_high_value_manual_payment_requires_approval(session, tenant, account, invoice):
    item = _manual(session, tenant, account, amount="100000.00")
    session.commit()
    assert item.requires_approval is True
    submit_manual_payment(session, tenant.id, item.id, submitted_by="operator")
    session.commit()
    assert session.get(ManualPayment, item.id).status == "UNDER_REVIEW"
    approve_manual_payment(session, tenant.id, item.id, approved_by="finance-manager")
    session.commit()
    txn = post_manual_payment(session, tenant.id, item.id, correlation_id="c1")
    session.commit()
    assert txn.kind == "CAPTURE"
    assert session.get(RevenueInvoice, invoice.id).status == "PAID"
    assert session.query(PaymentTransaction).filter(PaymentTransaction.tenant_id == tenant.id).count() == 1


def test_rejected_manual_payment_cannot_post(session, tenant, account, invoice):
    item = _manual(session, tenant, account, amount="50000.00")
    session.commit()
    submit_manual_payment(session, tenant.id, item.id, submitted_by="operator")
    reject_manual_payment(session, tenant.id, item.id, reason="slip unreadable")
    session.commit()
    assert session.get(ManualPayment, item.id).status == "REJECTED"
    with pytest.raises(ValueError):
        post_manual_payment(session, tenant.id, item.id, correlation_id="c1")


def test_posted_manual_payment_can_reverse(session, tenant, account, invoice):
    item = _manual(session, tenant, account, amount="100.00")
    session.commit()
    submit_manual_payment(session, tenant.id, item.id, submitted_by="operator")
    approve_manual_payment(session, tenant.id, item.id, approved_by="finance")
    session.commit()
    post_manual_payment(session, tenant.id, item.id, correlation_id="c1")
    session.commit()
    reverse_manual_payment(session, tenant.id, item.id, reversed_by="finance", reason="duplicate", correlation_id="c1")
    session.commit()
    assert session.get(ManualPayment, item.id).status == "REVERSED"
