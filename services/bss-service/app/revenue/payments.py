"""Core payment flow: server-side intents, gateway orders, idempotent capture,
allocation, ledger posting, receipts and restoration eligibility.

Invariants:
- Amounts are always server-calculated; frontend amounts are never trusted.
- A payment is confirmed once (unique tenant+idempotency_key and tenant+external_ref).
- sum(payment allocations) + credit <= confirmed payment amount.
- Invoice status is DERIVED from allocations, never patched by a capture event."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .enums import INVOICE_STATES
from .events import publish_outbox
from .gateways import get_gateway_class
from .ledger import post_entry
from .models import (
    BillingAccount,
    GatewayAccount,
    InvoiceLineItem,
    PaymentAllocation,
    PaymentAttempt,
    PaymentIntent,
    PaymentTransaction,
    Receipt,
    RevenueInvoice,
)
from .money import MoneyError, money, normalize_currency
from .state_machine import attempt_transition, intent_transition

PAID_LEDGER_ACCOUNTS = {
    "AR_GATEWAY": ("AR - Gateway receivable", "ASSET", "DEBIT"),
    "PAYMENT_INCOME": ("Revenue - payments received", "REVENUE", "CREDIT"),
    "UNALLOCATED_CREDIT": ("Liability - unallocated credit", "LIABILITY", "CREDIT"),
    "BANK": ("Asset - bank", "ASSET", "DEBIT"),
    "REFUNDS_PAYABLE": ("Liability - refunds payable", "LIABILITY", "CREDIT"),
    "CHARGEBACK_LOSS": ("Expense - chargeback losses", "EXPENSE", "DEBIT"),
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PaymentError(ValueError):
    pass


def next_receipt_number(prefix: str = "RCP") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12].upper()}"


def account_or_404(session: Session, tenant_id, billing_account_id) -> BillingAccount:
    account = session.scalar(select(BillingAccount).where(BillingAccount.id == billing_account_id, BillingAccount.tenant_id == tenant_id))
    if account is None:
        raise PaymentError("billing account not found")
    return account


def invoice_or_404(session: Session, tenant_id, invoice_id) -> RevenueInvoice:
    invoice = session.scalar(select(RevenueInvoice).where(RevenueInvoice.id == invoice_id, RevenueInvoice.tenant_id == tenant_id))
    if invoice is None:
        raise PaymentError("invoice not found")
    return invoice


def server_side_payable(session: Session, tenant_id, account: BillingAccount, invoice_ids: list | None = None) -> Decimal:
    """Compute the payable amount server-side for the eligible invoices."""
    invoices = list(
        session.scalars(
            select(RevenueInvoice).where(
                RevenueInvoice.tenant_id == tenant_id,
                RevenueInvoice.billing_account_id == account.id,
                RevenueInvoice.status.in_(["ISSUED", "PARTIALLY_PAID", "OVERDUE"]),
            )
        )
    )
    if invoice_ids:
        wanted = set(uuid.UUID(str(v)) for v in invoice_ids)
        invoices = [inv for inv in invoices if inv.id in wanted]
        if len(invoices) != len(wanted):
            raise PaymentError("one or more requested invoices are not payable")
    return sum((inv.total_amount - inv.paid_amount - inv.written_off_amount for inv in invoices), Decimal("0.00"))


def create_payment_intent(
    session: Session,
    tenant_id,
    *,
    billing_account_id,
    amount: Decimal | None = None,
    currency: str = "INR",
    invoice_ids: list | None = None,
    description: str | None = None,
    idempotency_key: str,
    correlation_id: str,
    gateway_account_id: uuid.UUID | None = None,
    allow_overpayment: bool = False,
    created_by: str = "system",
    ttl_seconds: int = 1800,
) -> PaymentIntent:
    currency = normalize_currency(currency)
    account = account_or_404(session, tenant_id, billing_account_id)
    if account.currency != currency:
        raise PaymentError(f"currency mismatch: account uses {account.currency}")
    existing = session.scalar(select(PaymentIntent).where(PaymentIntent.tenant_id == tenant_id, PaymentIntent.idempotency_key == idempotency_key))
    if existing is not None:
        return existing
    payable = server_side_payable(session, tenant_id, account, invoice_ids)
    if amount is None:
        amount = payable
    else:
        amount = money(amount)
        if not allow_overpayment and amount > payable:
            raise PaymentError(f"amount exceeds server-side payable {payable}")
    if amount <= 0:
        raise PaymentError("amount must be positive")
    gateway = None
    if gateway_account_id:
        gateway = session.scalar(select(GatewayAccount).where(GatewayAccount.id == gateway_account_id, GatewayAccount.tenant_id == tenant_id))
        if gateway is None:
            raise PaymentError("gateway account not found")
    intent = PaymentIntent(
        tenant_id=tenant_id,
        billing_account_id=account.id,
        amount=amount,
        currency=currency,
        status="CREATED",
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
        gateway_account_id=gateway.id if gateway else None,
        invoice_ids=[str(v) for v in (invoice_ids or [])],
        description=description,
        created_by=created_by,
        expires_at=_now() + timedelta(seconds=ttl_seconds),
    )
    session.add(intent)
    session.flush()
    publish_outbox(session, "payment.intent_created.v1", {"payment_intent_id": str(intent.id), "amount": str(intent.amount), "currency": intent.currency, "billing_account_id": str(account.id)}, tenant_id, correlation_id, idempotency_key)
    return intent


def start_hosted_checkout(session: Session, tenant_id, intent_id, *, mode: str = "test") -> tuple[PaymentIntent, PaymentAttempt, dict]:
    """Create the gateway order and return only the safe checkout payload."""
    intent = session.scalar(select(PaymentIntent).where(PaymentIntent.id == intent_id, PaymentIntent.tenant_id == tenant_id))
    if intent is None:
        raise PaymentError("payment intent not found")
    if intent.status == "CREATED":
        intent.status = intent_transition(intent.status, "PENDING")
    elif intent.status == "CANCELLED":
        raise PaymentError("payment intent is cancelled")
    attempt = PaymentAttempt(tenant_id=tenant_id, payment_intent_id=intent.id, attempt_number=1, status="CREATED", mode=mode)
    session.add(attempt)
    session.flush()
    gateway = session.scalar(select(GatewayAccount).where(GatewayAccount.id == intent.gateway_account_id, GatewayAccount.tenant_id == tenant_id))
    if gateway is None:
        raise PaymentError("no gateway account configured for intent")
    gateway_class = get_gateway_class(gateway.gateway_code)
    adapter = gateway_class()
    order = adapter.create_payment(
        amount=intent.amount,
        currency=intent.currency,
        description=intent.description or "ISP payment",
        idempotency_key=intent.idempotency_key,
        account=gateway,
    )
    intent.gateway_order_ref = order.gateway_order_ref
    attempt.status = attempt_transition(attempt.status, "SUBMITTED")
    attempt.gateway_ref = order.gateway_order_ref
    attempt.submitted_at = _now()
    session.flush()
    publish_outbox(session, "payment.pending.v1", {"payment_intent_id": str(intent.id), "gateway_order_ref": order.gateway_order_ref}, tenant_id, intent.correlation_id)
    return intent, attempt, order.safe_payload


def capture_payment(
    session: Session,
    tenant_id,
    *,
    intent_id,
    external_ref: str,
    amount: Decimal,
    currency: str,
    method: str | None = None,
    mode: str = "test",
    idempotency_key: str,
    correlation_id: str,
    gateway_account_id: uuid.UUID | None = None,
) -> PaymentTransaction:
    """The authoritative, idempotent confirmation of a payment."""
    currency = normalize_currency(currency)
    amount = money(amount)
    intent = session.scalar(select(PaymentIntent).where(PaymentIntent.id == intent_id, PaymentIntent.tenant_id == tenant_id))
    if intent is None:
        raise PaymentError("payment intent not found")
    if intent.currency != currency:
        raise PaymentError(f"currency mismatch: intent uses {intent.currency}")
    existing_txn = session.scalar(select(PaymentTransaction).where(PaymentTransaction.tenant_id == tenant_id, PaymentTransaction.idempotency_key == idempotency_key))
    if existing_txn is not None:
        return existing_txn
    existing_ref = session.scalar(select(PaymentTransaction).where(PaymentTransaction.tenant_id == tenant_id, PaymentTransaction.external_ref == external_ref))
    if existing_ref is not None:
        if existing_ref.idempotency_key != idempotency_key:
            raise PaymentError("external reference already captured with a different idempotency key")
        return existing_ref
    if amount > intent.amount:
        raise PaymentError(f"captured amount {amount} exceeds intent amount {intent.amount}")
    if intent.status == "PAID":
        raise PaymentError("payment intent already paid")
    txn = PaymentTransaction(
        tenant_id=tenant_id,
        payment_intent_id=intent.id,
        billing_account_id=intent.billing_account_id,
        kind="CAPTURE",
        external_ref=external_ref,
        amount=amount,
        currency=currency,
        status="CONFIRMED",
        gateway_account_id=gateway_account_id,
        method=method,
        mode=mode,
        correlation_id=correlation_id,
        idempotency_key=idempotency_key,
    )
    session.add(txn)
    session.flush()
    # Mark intent + attempt captured.
    if intent.status in ("PENDING", "REQUIRES_ACTION", "PROCESSING", "PARTIALLY_PAID"):
        intent.status = intent_transition(intent.status, "PAID") if amount >= intent.amount else intent.status
        if amount < intent.amount:
            intent.status = "PARTIALLY_PAID"
    attempt = session.scalar(
        select(PaymentAttempt).where(PaymentAttempt.payment_intent_id == intent.id, PaymentAttempt.gateway_ref == external_ref).order_by(PaymentAttempt.created_at.desc()).limit(1)
    ) or session.scalar(select(PaymentAttempt).where(PaymentAttempt.payment_intent_id == intent.id).order_by(PaymentAttempt.created_at.desc()).limit(1))
    if attempt is not None and attempt.status not in ("CAPTURED", "REFUNDED", "CHARGEBACK"):
        _advance_attempt_to_captured(attempt)
        attempt.captured_at = _now()
    # Allocate oldest-invoice-first.
    account = account_or_404(session, tenant_id, intent.billing_account_id)
    allocations, unallocated = allocate_payment(session, tenant_id, txn, account, amount)
    # Post balanced ledger entries for the confirmed capture.
    post_capture_ledger(session, tenant_id, txn, amount, currency, correlation_id)
    # Receipt + events.
    receipt = Receipt(
        tenant_id=tenant_id,
        billing_account_id=account.id,
        transaction_id=txn.id,
        receipt_number=next_receipt_number(),
        currency=currency,
        amount=amount,
    )
    session.add(receipt)
    publish_outbox(session, "payment.captured.v1", {"payment_intent_id": str(intent.id), "transaction_id": str(txn.id), "amount": str(amount), "currency": currency}, tenant_id, correlation_id, idempotency_key)
    publish_outbox(session, "payment.allocated.v1" if unallocated == 0 else "payment.partially_allocated.v1", {"transaction_id": str(txn.id), "allocations": [str(a.invoice_id) for a in allocations], "unallocated": str(unallocated)}, tenant_id, correlation_id)
    if unallocated > 0:
        account.credit_balance = money(Decimal(account.credit_balance or 0) + unallocated)
        publish_outbox(session, "payment.unallocated_credit_created.v1", {"billing_account_id": str(account.id), "credit": str(unallocated), "currency": currency}, tenant_id, correlation_id)
    # Restoration eligibility: financial restriction is cleared for the resolved invoices.
    evaluate_restoration(session, tenant_id, account, correlation_id)
    session.flush()
    return txn


def allocate_payment(session: Session, tenant_id, txn: PaymentTransaction, account: BillingAccount, amount: Decimal) -> tuple[list[PaymentAllocation], Decimal]:
    """Allocate a confirmed payment to invoices (oldest due first). Enforces
    the invariant sum(allocations) <= confirmed amount. Returns (allocations, credit)."""
    currency = txn.currency
    invoices = list(
        session.scalars(
            select(RevenueInvoice)
            .where(
                RevenueInvoice.tenant_id == tenant_id,
                RevenueInvoice.billing_account_id == account.id,
                RevenueInvoice.status.in_(["ISSUED", "PARTIALLY_PAID", "OVERDUE"]),
            )
            .order_by(RevenueInvoice.due_date)
        )
    )
    remaining = amount
    allocations: list[PaymentAllocation] = []
    for invoice in invoices:
        if remaining <= 0:
            break
        due = invoice.total_amount - invoice.paid_amount - invoice.written_off_amount
        if due <= 0:
            continue
        applied = min(remaining, due)
        allocation = PaymentAllocation(
            tenant_id=tenant_id,
            transaction_id=txn.id,
            invoice_id=invoice.id,
            amount=applied,
            currency=currency,
            correlation_id=txn.correlation_id,
        )
        session.add(allocation)
        allocations.append(allocation)
        invoice.paid_amount = money(Decimal(invoice.paid_amount or 0) + applied)
        invoice.status = derive_invoice_status(invoice)
        remaining -= applied
    return allocations, money(remaining)


def derive_invoice_status(invoice: RevenueInvoice) -> str:
    due = invoice.total_amount - invoice.paid_amount - invoice.written_off_amount
    if due <= 0:
        return "PAID"
    if invoice.paid_amount > 0:
        return "PARTIALLY_PAID"
    return invoice.status


def _advance_attempt_to_captured(attempt: PaymentAttempt) -> None:
    """Walk the attempt through the validated path to CAPTURED."""
    path = ["SUBMITTED", "AUTHORIZED", "CAPTURE_PENDING", "CAPTURED"]
    if attempt.status in path:
        start = path.index(attempt.status)
        for target in path[start + 1:]:
            try:
                attempt.status = attempt_transition(attempt.status, target)
            except ValueError:
                break


def post_capture_ledger(session: Session, tenant_id, txn: PaymentTransaction, amount: Decimal, currency: str, correlation_id: str) -> None:
    post_entry(
        session,
        tenant_id,
        entry_type="PAYMENT_CAPTURE",
        currency=currency,
        lines=[("AR_GATEWAY", "DEBIT", amount), ("PAYMENT_INCOME", "CREDIT", amount)],
        correlation_id=correlation_id,
        description=f"payment capture {txn.external_ref}",
        source_event={"transaction_id": str(txn.id), "external_ref": txn.external_ref, "kind": "CAPTURE"},
        actor="payment-worker",
    )


def evaluate_restoration(session: Session, tenant_id, account: BillingAccount, correlation_id: str) -> dict:
    """Financial restoration eligibility: only the financial restriction the
    payment resolved is lifted. Fraud/admin/compliance holds still block it."""
    outstanding = server_side_payable(session, tenant_id, account)
    eligibility = {
        "restoration_eligible": outstanding <= 0,
        "remaining_overdue": str(outstanding),
        "blocked_by_holds": [hold for hold in (account.holds or []) if hold in ("FRAUD", "ADMINISTRATIVE", "COMPLIANCE", "CHARGEBACK")],
    }
    eligibility["restoration_eligible"] = eligibility["restoration_eligible"] and not eligibility["blocked_by_holds"]
    if eligibility["restoration_eligible"]:
        publish_outbox(session, "billing.restoration_eligible.v1", {"billing_account_id": str(account.id), "customer_ref": account.customer_ref, "remaining_overdue": "0"}, tenant_id, correlation_id)
    return eligibility
