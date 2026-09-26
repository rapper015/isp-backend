"""Idempotent recurring invoice generation for active billing schedules."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .models import BillingAccount, BillingAccountItem, BillingRun, Invoice, Plan

LOCK_ID = 4_218_551_001


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def run_due_billing(session: Session, now: datetime | None = None, tenant_id=None) -> dict:
    now = now or utcnow()
    locked = session.scalar(text("SELECT pg_try_advisory_xact_lock(:lock_id)"), {"lock_id": LOCK_ID})
    if not locked:
        return {"status": "skipped", "reason": "another billing run is active", "generated": 0, "failed": 0}

    run = BillingRun(status="running")
    session.add(run)
    session.flush()
    statement = select(BillingAccount).where(
        BillingAccount.status == "active",
        BillingAccount.auto_invoice.is_(True),
        BillingAccount.next_invoice_at <= now,
    ).with_for_update(skip_locked=True)
    if tenant_id is not None:
        statement = statement.where(BillingAccount.tenant_id == tenant_id)
    accounts = list(session.scalars(statement.order_by(BillingAccount.next_invoice_at).limit(1000)))
    generated = 0
    failures: list[dict] = []
    for account in accounts:
        items = list(session.scalars(select(BillingAccountItem).where(
            BillingAccountItem.billing_account_id == account.id,
            BillingAccountItem.status == "active",
            BillingAccountItem.effective_from <= now,
            (BillingAccountItem.effective_until.is_(None)) | (BillingAccountItem.effective_until > now),
        ).order_by(BillingAccountItem.created_at)))
        if not items:
            failures.append({"account_number": account.account_number, "error": "billing account has no active subscriber charges"})
            continue
        period_start = account.next_invoice_at
        period_end = period_start + timedelta(days=account.cycle_days) - timedelta(seconds=1)
        invoice_number = f"INV-{account.account_number[-24:]}-{period_start:%Y%m%d}"
        if session.scalar(select(Invoice.id).where(Invoice.invoice_number == invoice_number)):
            account.last_invoice_at = period_start
            account.next_invoice_at = period_start + timedelta(days=account.cycle_days)
            continue
        subtotal = Decimal("0")
        line_items = []
        plan_ids = []
        for item in items:
            plan = session.get(Plan, item.plan_id)
            if plan is None or plan.status.lower() != "active":
                failures.append({"account_number": account.account_number, "subscriber_id": str(item.subscriber_id), "error": "plan is missing or inactive"})
                continue
            charge = item.custom_amount if item.custom_amount is not None else plan.monthly_fee
            subtotal += charge
            plan_ids.append(plan.id)
            line_items.append({
                "type": "subscription", "subscriber_id": str(item.subscriber_id), "plan_id": str(plan.id),
                "description": item.description or plan.name, "plan_name": plan.name,
                "quantity": 1, "unit_price": str(charge), "total": str(charge),
            })
        if not line_items:
            continue
        tax = (subtotal * account.tax_percent / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        total = subtotal + tax
        if tax:
            line_items.append({"type": "tax", "description": f"Tax ({account.tax_percent}%)", "quantity": 1, "unit_price": str(tax), "total": str(tax)})
        session.add(Invoice(
            invoice_number=invoice_number, tenant_id=account.tenant_id, customer_id=account.customer_id,
            billing_account_id=account.id, subscriber_id=items[0].subscriber_id if len(items) == 1 else None,
            plan_id=plan_ids[0], subtotal=subtotal, tax_amount=tax,
            amount=total, balance_due=total, status="issued", due_date=now + timedelta(days=account.due_days),
            billing_period_start=period_start, billing_period_end=period_end, line_items=line_items,
        ))
        account.last_invoice_at = period_start
        account.next_invoice_at = period_start + timedelta(days=account.cycle_days)
        generated += 1
    run.generated_count = generated
    run.failed_count = len(failures)
    run.detail = {"failures": failures}
    run.status = "completed" if not failures else "partial"
    run.completed_at = utcnow()
    session.commit()
    return {"status": run.status, "run_id": str(run.id), "generated": generated, "failed": len(failures), "failures": failures}


def refresh_overdue_invoices(session: Session, now: datetime | None = None, tenant_id=None) -> int:
    now = now or utcnow()
    statement = select(Invoice).where(
        Invoice.due_date < now, Invoice.balance_due > 0,
        Invoice.status.in_(["issued", "partially_paid"]),
    )
    if tenant_id is not None:
        statement = statement.where(Invoice.tenant_id == tenant_id)
    invoices = list(session.scalars(statement))
    for invoice in invoices:
        invoice.status = "overdue"
    session.commit()
    return len(invoices)
