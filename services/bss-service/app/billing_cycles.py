"""Idempotent recurring invoice generation for active billing schedules."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .models import BillingRun, BillingSchedule, Invoice, Plan

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
    statement = select(BillingSchedule).where(
        BillingSchedule.status == "active",
        BillingSchedule.auto_invoice.is_(True),
        BillingSchedule.next_invoice_at <= now,
    ).with_for_update(skip_locked=True)
    if tenant_id is not None:
        statement = statement.where(BillingSchedule.tenant_id == tenant_id)
    schedules = list(session.scalars(statement.order_by(BillingSchedule.next_invoice_at).limit(1000)))
    generated = 0
    failures: list[dict] = []
    for schedule in schedules:
        plan = session.get(Plan, schedule.plan_id)
        if plan is None or plan.status.lower() != "active":
            failures.append({"account_code": schedule.account_code, "error": "plan is missing or inactive"})
            continue
        period_start = schedule.next_invoice_at
        period_end = period_start + timedelta(days=schedule.cycle_days) - timedelta(seconds=1)
        invoice_number = f"INV-{schedule.account_code[-24:]}-{period_start:%Y%m%d}"
        if session.scalar(select(Invoice.id).where(Invoice.invoice_number == invoice_number)):
            schedule.last_invoice_at = period_start
            schedule.next_invoice_at = period_start + timedelta(days=schedule.cycle_days)
            continue
        subtotal = schedule.custom_amount if schedule.custom_amount is not None else plan.monthly_fee
        tax = (subtotal * schedule.tax_percent / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        total = subtotal + tax
        line_items = [{"description": plan.name, "quantity": 1, "unit_price": str(subtotal), "total": str(subtotal)}]
        if tax:
            line_items.append({"description": f"Tax ({schedule.tax_percent}%)", "quantity": 1, "unit_price": str(tax), "total": str(tax)})
        session.add(Invoice(
            invoice_number=invoice_number, tenant_id=schedule.tenant_id, customer_id=schedule.customer_id,
            subscriber_id=schedule.subscriber_id, plan_id=plan.id, subtotal=subtotal, tax_amount=tax,
            amount=total, balance_due=total, status="issued", due_date=now + timedelta(days=schedule.due_days),
            billing_period_start=period_start, billing_period_end=period_end, line_items=line_items,
        ))
        schedule.last_invoice_at = period_start
        schedule.next_invoice_at = period_start + timedelta(days=schedule.cycle_days)
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
