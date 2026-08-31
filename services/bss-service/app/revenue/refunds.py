"""Refunds and chargebacks.

Refunds create new immutable financial events and reverse/adjust allocations;
they never exceed the refundable captured amount. Chargebacks create new
immutable events and ledger postings — the original payment is never deleted."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from .events import publish_outbox
from .gateways import get_gateway_class
from .ledger import post_entry
from .models import BillingAccount, Dispute, GatewayAccount, PaymentAllocation, PaymentTransaction, Refund, RevenueInvoice
from .money import money, normalize_currency
from .payments import account_or_404


def _now() -> datetime:
    return datetime.now(timezone.utc)


def refundable_amount(session: Session, tenant_id, transaction_id) -> Decimal:
    captured = session.scalar(select(PaymentTransaction).where(PaymentTransaction.id == transaction_id, PaymentTransaction.tenant_id == tenant_id))
    if captured is None:
        raise ValueError("payment transaction not found")
    if captured.kind != "CAPTURE":
        raise ValueError("refunds apply to captured payments only")
    refunded = session.scalar(
        select(func.coalesce(func.sum(Refund.amount), 0)).where(
            Refund.tenant_id == tenant_id,
            Refund.transaction_id == transaction_id,
            Refund.status.in_(["PENDING", "COMPLETED"]),
        )
    )
    return money(Decimal(captured.amount) - Decimal(refunded or 0))


def create_refund(
    session: Session,
    tenant_id,
    *,
    transaction_id,
    amount,
    currency,
    reason,
    refund_reference,
    correlation_id,
    approved_by: str | None = None,
    requires_approval: bool = False,
) -> Refund:
    currency = normalize_currency(currency)
    amount = money(amount)
    txn = session.scalar(select(PaymentTransaction).where(PaymentTransaction.id == transaction_id, PaymentTransaction.tenant_id == tenant_id))
    if txn is None:
        raise ValueError("payment transaction not found")
    if txn.currency != currency:
        raise ValueError("currency mismatch")
    available = refundable_amount(session, tenant_id, transaction_id)
    if amount > available:
        raise ValueError(f"refund {amount} exceeds refundable amount {available}")
    existing = session.scalar(select(Refund).where(Refund.tenant_id == tenant_id, Refund.refund_reference == refund_reference))
    if existing is not None:
        return existing
    refund = Refund(
        tenant_id=tenant_id,
        billing_account_id=txn.billing_account_id,
        transaction_id=txn.id,
        refund_reference=refund_reference,
        amount=amount,
        currency=currency,
        status="PENDING",
        reason=reason,
        approved_by=approved_by,
        correlation_id=correlation_id,
    )
    session.add(refund)
    session.flush()
    publish_outbox(session, "payment.refund_requested.v1", {"refund_id": str(refund.id), "transaction_id": str(txn.id), "amount": str(amount)}, tenant_id, correlation_id, f"refund-request:{tenant_id}:{refund_reference}")
    if not requires_approval:
        complete_refund(session, tenant_id, refund.id, correlation_id=correlation_id)
    return refund


def complete_refund(session: Session, tenant_id, refund_id, *, correlation_id: str) -> Refund:
    refund = session.scalar(select(Refund).where(Refund.id == refund_id, Refund.tenant_id == tenant_id))
    if refund is None:
        raise ValueError("refund not found")
    if refund.status == "COMPLETED":
        return refund
    txn = session.scalar(select(PaymentTransaction).where(PaymentTransaction.id == refund.transaction_id, PaymentTransaction.tenant_id == tenant_id))
    gateway = None
    if txn.gateway_account_id:
        gateway = session.scalar(select(GatewayAccount).where(GatewayAccount.id == txn.gateway_account_id, GatewayAccount.tenant_id == tenant_id))
    if gateway is not None:
        adapter = get_gateway_class(gateway.gateway_code)()
        result = adapter.create_refund(txn.external_ref, refund.amount, refund.refund_reference)
        refund.gateway_refund_id = result.detail.get("refund_id")
    refund.status = "COMPLETED"
    # Reversal allocation: reduce invoice paid_amount by the refunded amount,
    # oldest-paid-first.
    _reverse_allocations(session, tenant_id, txn, refund.amount, correlation_id)
    post_entry(
        session,
        tenant_id,
        entry_type="PAYMENT_REFUND",
        currency=refund.currency,
        lines=[("PAYMENT_INCOME", "DEBIT", refund.amount), ("AR_GATEWAY", "CREDIT", refund.amount)],
        correlation_id=correlation_id,
        description=f"refund {refund.refund_reference}",
        source_event={"refund_id": str(refund.id), "transaction_id": str(txn.id)},
        actor=refund.approved_by,
    )
    publish_outbox(session, "payment.refunded.v1", {"refund_id": str(refund.id), "transaction_id": str(txn.id), "amount": str(refund.amount)}, tenant_id, correlation_id, f"refund-done:{tenant_id}:{refund.refund_reference}")
    session.flush()
    return refund


def _reverse_allocations(session: Session, tenant_id, txn: PaymentTransaction, amount: Decimal, correlation_id: str) -> None:
    allocations = list(
        session.scalars(
            select(PaymentAllocation).where(PaymentAllocation.tenant_id == tenant_id, PaymentAllocation.transaction_id == txn.id, PaymentAllocation.reversal_of.is_(None)).order_by(PaymentAllocation.created_at.desc())
        )
    )
    remaining = amount
    for allocation in allocations:
        if remaining <= 0:
            break
        reversed_amount = min(remaining, allocation.amount)
        session.add(
            PaymentAllocation(
                tenant_id=tenant_id,
                transaction_id=txn.id,
                invoice_id=allocation.invoice_id,
                amount=money(-reversed_amount),
                currency=allocation.currency,
                reversal_of=allocation.id,
                correlation_id=correlation_id,
            )
        )
        invoice = session.get(RevenueInvoice, allocation.invoice_id)
        if invoice is not None:
            invoice.paid_amount = money(Decimal(invoice.paid_amount or 0) - reversed_amount)
            from .payments import derive_invoice_status

            invoice.status = derive_invoice_status(invoice)
        remaining -= reversed_amount


def complete_refund_from_webhook(session: Session, tenant_id, webhook, payload: dict) -> Refund | None:
    external_ref = payload.get("external_ref") or payload.get("payment_id")
    if not external_ref:
        return None
    txn = session.scalar(select(PaymentTransaction).where(PaymentTransaction.tenant_id == tenant_id, PaymentTransaction.external_ref == external_ref))
    if txn is None:
        return None
    amount = money(payload.get("amount", 0))
    if amount <= 0:
        return None
    refund = session.scalar(select(Refund).where(Refund.tenant_id == tenant_id, Refund.transaction_id == txn.id, Refund.gateway_refund_id == payload.get("refund_id")))
    if refund is None:
        refund = create_refund(session, tenant_id, transaction_id=txn.id, amount=amount, currency=txn.currency, reason="gateway webhook refund", refund_reference=f"RFD-{uuid.uuid4().hex[:12].upper()}", correlation_id=webhook.correlation_id, approved_by="gateway")
    return complete_refund(session, tenant_id, refund.id, correlation_id=webhook.correlation_id)


def create_chargeback(
    session: Session,
    tenant_id,
    *,
    transaction_id,
    gateway_dispute_ref,
    amount,
    currency,
    reason,
    evidence_deadline,
    correlation_id,
) -> Dispute:
    currency = normalize_currency(currency)
    amount = money(amount)
    txn = session.scalar(select(PaymentTransaction).where(PaymentTransaction.id == transaction_id, PaymentTransaction.tenant_id == tenant_id))
    if txn is None:
        raise ValueError("payment transaction not found")
    existing = session.scalar(select(Dispute).where(Dispute.tenant_id == tenant_id, Dispute.gateway_dispute_ref == gateway_dispute_ref))
    if existing is not None:
        return existing
    dispute = Dispute(
        tenant_id=tenant_id,
        billing_account_id=txn.billing_account_id,
        transaction_id=txn.id,
        gateway_dispute_ref=gateway_dispute_ref,
        amount=amount,
        currency=currency,
        reason=reason,
        status="OPEN",
        evidence_deadline=evidence_deadline,
        correlation_id=correlation_id,
    )
    session.add(dispute)
    session.flush()
    account = account_or_404(session, tenant_id, txn.billing_account_id)
    holds = list(account.holds or [])
    if "CHARGEBACK" not in holds:
        holds.append("CHARGEBACK")
    account.holds = holds
    # Chargeback posts a NEW immutable event; the original payment stays intact.
    post_entry(
        session,
        tenant_id,
        entry_type="CHARGEBACK",
        currency=currency,
        lines=[("CHARGEBACK_LOSS", "DEBIT", amount), ("AR_GATEWAY", "CREDIT", amount)],
        correlation_id=correlation_id,
        description=f"chargeback {gateway_dispute_ref}",
        source_event={"dispute_id": str(dispute.id), "transaction_id": str(txn.id)},
        actor="payment-worker",
    )
    publish_outbox(session, "payment.chargeback_received.v1", {"dispute_id": str(dispute.id), "transaction_id": str(txn.id), "amount": str(amount), "currency": currency}, tenant_id, correlation_id, f"chargeback:{tenant_id}:{gateway_dispute_ref}")
    session.flush()
    return dispute
