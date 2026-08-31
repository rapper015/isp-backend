"""Operational financial reports derived from financial records and ledger
projections — never from mutable dashboard counters."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import (
    BillingAccount,
    Dispute,
    LedgerBalanceProjection,
    ManualPayment,
    PaymentAllocation,
    PaymentTransaction,
    Receipt,
    ReconciliationException,
    Refund,
    RevenueInvoice,
    Settlement,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def daily_collections(session: Session, tenant_id, days: int = 30) -> list[dict]:
    """Collections per day from immutable receipts (one per confirmed capture)."""
    start = _now()
    rows = []
    receipts = list(session.scalars(select(Receipt).where(Receipt.tenant_id == tenant_id)))
    by_day: dict[str, Decimal] = {}
    for receipt in receipts:
        day = receipt.issued_at.strftime("%Y-%m-%d")
        by_day[day] = by_day.get(day, Decimal("0.00")) + receipt.amount
    for day in sorted(by_day)[-days:]:
        rows.append({"date": day, "amount": str(by_day[day])})
    return rows


def invoice_aging(session: Session, tenant_id) -> list[dict]:
    buckets = {"current": 0, "1-30": 0, "31-60": 0, "61-90": 0, "90+": 0}
    now = _now()
    invoices = list(
        session.scalars(
            select(RevenueInvoice).where(
                RevenueInvoice.tenant_id == tenant_id,
                RevenueInvoice.status.in_(["ISSUED", "PARTIALLY_PAID", "OVERDUE"]),
            )
        )
    )
    for invoice in invoices:
        due = invoice.total_amount - invoice.paid_amount - invoice.written_off_amount
        if due <= 0:
            continue
        days = max(0, (now - invoice.due_date).days)
        key = "current" if days <= 0 else "1-30" if days <= 30 else "31-60" if days <= 60 else "61-90" if days <= 90 else "90+"
        buckets[key] += float(due)
    return buckets


def payment_method_summary(session: Session, tenant_id) -> dict:
    rows = session.execute(
        select(PaymentTransaction.method, func.count(), func.coalesce(func.sum(PaymentTransaction.amount), 0))
        .where(PaymentTransaction.tenant_id == tenant_id, PaymentTransaction.kind == "CAPTURE")
        .group_by(PaymentTransaction.method)
    )
    return {str(method or "UNKNOWN"): {"count": count, "amount": str(amount)} for method, count, amount in rows}


def refund_summary(session: Session, tenant_id) -> dict:
    total = session.scalar(select(func.coalesce(func.sum(Refund.amount), 0)).where(Refund.tenant_id == tenant_id, Refund.status == "COMPLETED"))
    return {"total_refunded": str(total or 0), "count": session.scalar(select(func.count()).select_from(Refund).where(Refund.tenant_id == tenant_id))}


def chargeback_summary(session: Session, tenant_id) -> dict:
    return {
        "count": session.scalar(select(func.count()).select_from(Dispute).where(Dispute.tenant_id == tenant_id)),
        "total": str(session.scalar(select(func.coalesce(func.sum(Dispute.amount), 0)).where(Dispute.tenant_id == tenant_id)) or 0),
    }


def settlement_summary(session: Session, tenant_id) -> dict:
    return {
        "count": session.scalar(select(func.count()).select_from(Settlement).where(Settlement.tenant_id == tenant_id)),
        "net_amount": str(session.scalar(select(func.coalesce(func.sum(Settlement.net_amount), 0)).where(Settlement.tenant_id == tenant_id)) or 0),
        "fees": str(session.scalar(select(func.coalesce(func.sum(Settlement.fee_amount), 0)).where(Settlement.tenant_id == tenant_id)) or 0),
    }


def recon_exception_summary(session: Session, tenant_id) -> dict:
    return {
        "open": session.scalar(select(func.count()).select_from(ReconciliationException).where(ReconciliationException.tenant_id == tenant_id, ReconciliationException.status == "OPEN")),
        "resolved": session.scalar(select(func.count()).select_from(ReconciliationException).where(ReconciliationException.tenant_id == tenant_id, ReconciliationException.status == "RESOLVED")),
    }


def credit_balance_report(session: Session, tenant_id) -> list[dict]:
    return [
        {"billing_account_id": str(item.id), "customer_ref": item.customer_ref, "credit_balance": str(item.credit_balance), "currency": item.currency}
        for item in session.scalars(select(BillingAccount).where(BillingAccount.tenant_id == tenant_id, BillingAccount.credit_balance > 0).order_by(BillingAccount.credit_balance.desc()))
    ]


def outstanding_report(session: Session, tenant_id) -> list[dict]:
    rows = []
    invoices = list(
        session.scalars(
            select(RevenueInvoice).where(
                RevenueInvoice.tenant_id == tenant_id,
                RevenueInvoice.status.in_(["ISSUED", "PARTIALLY_PAID", "OVERDUE"]),
            )
        )
    )
    for invoice in invoices:
        due = invoice.total_amount - invoice.paid_amount - invoice.written_off_amount
        if due > 0:
            rows.append({"invoice_number": invoice.invoice_number, "billing_account_id": str(invoice.billing_account_id), "due": str(due), "currency": invoice.currency, "due_date": invoice.due_date})
    return rows


def ledger_balances_report(session: Session, tenant_id, period_key: str | None = None) -> list[dict]:
    stmt = select(LedgerBalanceProjection).where(LedgerBalanceProjection.tenant_id == tenant_id)
    if period_key:
        stmt = stmt.where(LedgerBalanceProjection.period_key == period_key)
    return [{"account_id": str(item.account_id), "period_key": item.period_key, "balance": str(item.balance)} for item in session.scalars(stmt)]
