"""Manual/offline payments with maker-checker approval.

A submitted manual payment never restores service unless policy allows and
required approval is complete. Posting creates an immutable transaction,
allocations and ledger entries."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .events import publish_outbox
from .ledger import post_entry
from .models import ManualPayment, PaymentTransaction
from .money import money, normalize_currency
from .payments import account_or_404, allocate_payment
from .state_machine import manual_payment_transition


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_manual_payment(
    session: Session,
    tenant_id,
    *,
    billing_account_id,
    method: str,
    amount,
    currency: str,
    external_reference: str | None,
    payment_date,
    collector,
    branch_reference,
    evidence,
    notes,
    reference_number,
    correlation_id,
    approval_threshold: Decimal = Decimal("5000.00"),
) -> ManualPayment:
    currency = normalize_currency(currency)
    amount = money(amount)
    account = account_or_404(session, tenant_id, billing_account_id)
    existing = session.scalar(select(ManualPayment).where(ManualPayment.tenant_id == tenant_id, ManualPayment.reference_number == reference_number))
    if existing is not None:
        raise ValueError("manual payment reference already exists")
    requires_approval = amount >= approval_threshold or method in ("CASH", "CHEQUE")
    item = ManualPayment(
        tenant_id=tenant_id,
        billing_account_id=account.id,
        reference_number=reference_number,
        method=method,
        amount=amount,
        currency=currency,
        external_reference=external_reference,
        payment_date=payment_date,
        collector=collector,
        branch_reference=branch_reference,
        evidence=evidence or {},
        notes=notes,
        status="DRAFT",
        requires_approval=requires_approval,
        correlation_id=correlation_id,
    )
    session.add(item)
    session.flush()
    return item


def submit_manual_payment(session: Session, tenant_id, manual_payment_id, *, submitted_by: str) -> ManualPayment:
    item = session.scalar(select(ManualPayment).where(ManualPayment.id == manual_payment_id, ManualPayment.tenant_id == tenant_id))
    if item is None:
        raise ValueError("manual payment not found")
    item.status = manual_payment_transition(item.status, "SUBMITTED")
    item.submitted_by = submitted_by
    if item.requires_approval:
        item.status = manual_payment_transition(item.status, "UNDER_REVIEW")
    return item


def approve_manual_payment(session: Session, tenant_id, manual_payment_id, *, approved_by: str) -> ManualPayment:
    item = session.scalar(select(ManualPayment).where(ManualPayment.id == manual_payment_id, ManualPayment.tenant_id == tenant_id))
    if item is None:
        raise ValueError("manual payment not found")
    item.status = manual_payment_transition(item.status, "APPROVED")
    item.approved_by = approved_by
    return item


def reject_manual_payment(session: Session, tenant_id, manual_payment_id, *, reason: str) -> ManualPayment:
    item = session.scalar(select(ManualPayment).where(ManualPayment.id == manual_payment_id, ManualPayment.tenant_id == tenant_id))
    if item is None:
        raise ValueError("manual payment not found")
    item.status = manual_payment_transition(item.status, "REJECTED")
    item.approval_reason = reason
    return item


def post_manual_payment(session: Session, tenant_id, manual_payment_id, *, correlation_id: str) -> PaymentTransaction:
    """Post an approved manual payment: immutable transaction + allocations +
    ledger. Only an APPROVED manual payment can be posted."""
    item = session.scalar(select(ManualPayment).where(ManualPayment.id == manual_payment_id, ManualPayment.tenant_id == tenant_id))
    if item is None:
        raise ValueError("manual payment not found")
    if item.status != "APPROVED":
        raise ValueError(f"manual payment must be APPROVED before posting (state: {item.status})")
    txn = PaymentTransaction(
        tenant_id=tenant_id,
        payment_intent_id=uuid.uuid4(),  # manual payments are not tied to an online intent
        billing_account_id=item.billing_account_id,
        kind="CAPTURE",
        external_ref=item.reference_number,
        amount=item.amount,
        currency=item.currency,
        status="CONFIRMED",
        method=f"OFFLINE_{item.method}",
        mode="live",
        correlation_id=correlation_id,
        idempotency_key=f"manual:{tenant_id}:{item.reference_number}",
    )
    session.add(txn)
    session.flush()
    account = account_or_404(session, tenant_id, item.billing_account_id)
    allocations, unallocated = allocate_payment(session, tenant_id, txn, account, item.amount)
    post_entry(
        session,
        tenant_id,
        entry_type="MANUAL_PAYMENT",
        currency=item.currency,
        lines=[("AR_GATEWAY", "DEBIT", item.amount), ("PAYMENT_INCOME", "CREDIT", item.amount)],
        correlation_id=correlation_id,
        description=f"manual payment {item.reference_number} ({item.method})",
        source_event={"manual_payment_id": str(item.id), "external_ref": item.reference_number},
        actor=item.approved_by,
    )
    item.status = manual_payment_transition(item.status, "POSTED")
    if unallocated > 0:
        account.credit_balance = money(Decimal(account.credit_balance or 0) + unallocated)
    publish_outbox(session, "payment.captured.v1", {"payment_intent_id": None, "transaction_id": str(txn.id), "amount": str(item.amount), "currency": item.currency, "manual": True}, tenant_id, correlation_id, f"manual:{tenant_id}:{item.reference_number}")
    from .payments import evaluate_restoration

    evaluate_restoration(session, tenant_id, account, correlation_id)
    session.flush()
    return txn


def reverse_manual_payment(session: Session, tenant_id, manual_payment_id, *, reversed_by: str, reason: str, correlation_id: str) -> ManualPayment:
    item = session.scalar(select(ManualPayment).where(ManualPayment.id == manual_payment_id, ManualPayment.tenant_id == tenant_id))
    if item is None:
        raise ValueError("manual payment not found")
    if item.status != "POSTED":
        raise ValueError("only POSTED manual payments can be reversed")
    item.status = manual_payment_transition(item.status, "REVERSED")
    item.approval_reason = reason
    publish_outbox(session, "payment.refunded.v1", {"manual_payment_id": str(item.id), "amount": str(item.amount), "reason": reason}, tenant_id, correlation_id, f"reverse:{tenant_id}:{item.reference_number}")
    return item
