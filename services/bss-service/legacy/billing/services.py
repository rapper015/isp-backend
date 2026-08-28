from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.utils import timezone

from aaa.exceptions import AppError
from plans.models import Plan
from subscribers.models import Subscriber

from .models import BillingAccount, BillingSettings, Invoice, LedgerEntry
from .sequences import next_invoice_number

TWO_PLACES = Decimal("0.01")

_SUBSCRIBER_STATUS_TO_BILLING_STATUS = {
    "terminated": BillingAccount.Status.CLOSED,
    "suspended": BillingAccount.Status.SUSPENDED,
    "inactive": BillingAccount.Status.INACTIVE,
}


def round2(value) -> Decimal:
    return Decimal(value).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def map_subscriber_status_to_billing_status(status: str) -> str:
    return _SUBSCRIBER_STATUS_TO_BILLING_STATUS.get(status, BillingAccount.Status.ACTIVE)


def next_invoice_date(billing_day: int, from_date):
    year = from_date.year
    month = from_date.month + 1
    if month > 12:
        month = 1
        year += 1
    return from_date.replace(
        year=year, month=month, day=billing_day, hour=0, minute=0, second=0, microsecond=0
    )


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def get_settings() -> BillingSettings:
    settings_obj, _ = BillingSettings.objects.get_or_create(settings_key="default")
    return settings_obj


def update_settings(payload: dict) -> BillingSettings:
    settings_obj = get_settings()
    for field in (
        "invoice_prefix",
        "default_due_days",
        "grace_period_days",
        "tax_percent",
        "suspension_enabled",
        "auto_suspend_on_overdue",
        "default_billing_day",
        "currency",
    ):
        if field in payload:
            setattr(settings_obj, field, payload[field])
    settings_obj.save()
    return settings_obj


# ---------------------------------------------------------------------------
# Billing accounts (derived/cached projection of Subscriber state)
# ---------------------------------------------------------------------------


def sync_billing_accounts(subscriber_ids=None):
    settings_obj = get_settings()
    qs = Subscriber.objects.select_related("customer", "plan").order_by("-created_at")
    if subscriber_ids:
        qs = qs.filter(id__in=subscriber_ids)

    accounts = []
    for subscriber in qs:
        account, created = BillingAccount.objects.get_or_create(
            subscriber=subscriber,
            defaults={
                "account_code": f"BA-{subscriber.subscriber_code}",
                "customer": subscriber.customer,
                "plan": subscriber.plan,
                "outstanding_balance": subscriber.current_balance,
                "status": map_subscriber_status_to_billing_status(subscriber.status),
                "billing_day": settings_obj.default_billing_day,
                "due_days": settings_obj.default_due_days,
                "grace_period_days": settings_obj.grace_period_days,
                "suspension_enabled": settings_obj.suspension_enabled,
                "auto_generate_invoices": True,
            },
        )
        if not created:
            account.customer = subscriber.customer
            account.plan = subscriber.plan
            account.outstanding_balance = subscriber.current_balance
            account.status = map_subscriber_status_to_billing_status(subscriber.status)
            account.save()
        accounts.append(account)
    return accounts


def list_accounts():
    sync_billing_accounts()
    return (
        BillingAccount.objects.select_related("customer", "subscriber", "plan")
        .filter(deleted_at__isnull=True)
        .order_by("-created_at")
    )


def update_account_plan(account_id, plan_id, sync_subscriber_plan: bool = False) -> BillingAccount:
    plan = Plan.objects.filter(id=plan_id, deleted_at__isnull=True).first()
    if plan is None:
        raise AppError("Plan not found", 400)

    account = (
        BillingAccount.objects.select_related("subscriber")
        .filter(id=account_id, deleted_at__isnull=True)
        .first()
    )
    if account is None:
        raise AppError("Billing account not found", 404)

    account.plan = plan
    account.save(update_fields=["plan"])

    if sync_subscriber_plan:
        subscriber = account.subscriber
        subscriber.plan = plan
        subscriber.save(update_fields=["plan"])

    return account


def delete_billing_account(account_id) -> None:
    account = BillingAccount.objects.filter(id=account_id, deleted_at__isnull=True).first()
    if account is None:
        raise AppError("Billing account not found", 404)
    account.deleted_at = timezone.now()
    account.save(update_fields=["deleted_at"])


# ---------------------------------------------------------------------------
# Shared balance helpers
# ---------------------------------------------------------------------------


def apply_balance_impact(subscriber_id, delta: Decimal) -> None:
    subscriber = Subscriber.objects.filter(id=subscriber_id).first()
    if subscriber is None:
        return
    subscriber.current_balance = max(Decimal("0"), round2(subscriber.current_balance + delta))
    subscriber.save(update_fields=["current_balance"])


def touch_billing_account(
    subscriber_id, outstanding_delta: Decimal, last_invoice_at=None, next_invoice_dt=None
) -> None:
    account = BillingAccount.objects.filter(subscriber_id=subscriber_id).first()
    if account is None:
        return

    account.outstanding_balance = max(
        Decimal("0"), round2(account.outstanding_balance + outstanding_delta)
    )
    update_fields = ["outstanding_balance"]
    if last_invoice_at is not None:
        account.last_invoice_at = last_invoice_at
        update_fields.append("last_invoice_at")
    if next_invoice_dt is not None:
        account.next_invoice_date = next_invoice_dt
        update_fields.append("next_invoice_date")
    account.save(update_fields=update_fields)


def get_subscriber_billing_context(subscriber_id):
    subscriber = (
        Subscriber.objects.select_related("plan", "customer").filter(id=subscriber_id).first()
    )
    if subscriber is None:
        raise AppError("Subscriber not found", 404)

    billing_account = BillingAccount.objects.filter(subscriber_id=subscriber_id).first()
    plan = billing_account.plan if billing_account and billing_account.plan_id else subscriber.plan
    if plan is None:
        raise AppError("Plan not found", 400)

    return subscriber, plan, billing_account


# ---------------------------------------------------------------------------
# Invoicing
# ---------------------------------------------------------------------------


@transaction.atomic
def generate_invoice(input_data: dict) -> Invoice:
    settings_obj = get_settings()
    subscriber, plan, billing_account = get_subscriber_billing_context(input_data["subscriber_id"])

    period_start = input_data["billing_period_start"]
    period_end = input_data["billing_period_end"]

    due_days = billing_account.due_days if billing_account else settings_obj.default_due_days
    due_date = input_data.get("due_date") or (period_end + timedelta(days=due_days))

    duplicate_exists = (
        Invoice.objects.filter(
            subscriber=subscriber,
            billing_period_start=period_start,
            billing_period_end=period_end,
        )
        .exclude(status__in=[Invoice.Status.VOID, Invoice.Status.CANCELLED])
        .exists()
    )
    if duplicate_exists:
        raise AppError("Invoice already exists for this billing period", 400)

    subtotal = round2(plan.monthly_fee)
    tax_amount = round2(subtotal * settings_obj.tax_percent / Decimal(100))
    amount = round2(subtotal + tax_amount)

    invoice = Invoice.objects.create(
        invoice_number=next_invoice_number(settings_obj.invoice_prefix),
        customer=subscriber.customer,
        subscriber=subscriber,
        plan=plan,
        billing_period_start=period_start,
        billing_period_end=period_end,
        due_date=due_date,
        subtotal=subtotal,
        tax_amount=tax_amount,
        amount=amount,
        balance_due=amount,
        status=Invoice.Status.ISSUED,
        notes=input_data.get("notes") or "",
        line_items=[
            {
                "description": f"{plan.name} monthly subscription",
                "quantity": 1,
                "unit_price": subtotal,
                "total": subtotal,
            }
        ],
    )

    LedgerEntry.objects.create(
        customer=subscriber.customer,
        subscriber=subscriber,
        invoice=invoice,
        entry_type=LedgerEntry.EntryType.INVOICE,
        debit=amount,
        credit=Decimal("0"),
        balance_impact=amount,
        description=f"Invoice {invoice.invoice_number} generated",
        posted_at=timezone.now(),
    )

    apply_balance_impact(subscriber.id, amount)
    billing_day = billing_account.billing_day if billing_account else settings_obj.default_billing_day
    touch_billing_account(
        subscriber.id,
        outstanding_delta=amount,
        last_invoice_at=timezone.now(),
        next_invoice_dt=next_invoice_date(billing_day, period_end),
    )

    return invoice


@transaction.atomic
def update_invoice(invoice_id, payload: dict) -> Invoice:
    invoice = (
        Invoice.objects.select_related("subscriber", "customer").filter(id=invoice_id).first()
    )
    if invoice is None:
        raise AppError("Invoice not found", 404)

    if payload.get("due_date") is not None:
        invoice.due_date = payload["due_date"]

    if "notes" in payload:
        invoice.notes = payload["notes"] or ""

    new_status = payload.get("status")
    if new_status:
        if (
            new_status in (Invoice.Status.PAID, Invoice.Status.VOID, Invoice.Status.CANCELLED)
            and invoice.balance_due > 0
        ):
            cleared_amount = invoice.balance_due
            LedgerEntry.objects.create(
                customer=invoice.customer,
                subscriber=invoice.subscriber,
                invoice=invoice,
                entry_type=LedgerEntry.EntryType.ADJUSTMENT,
                debit=Decimal("0"),
                credit=cleared_amount,
                balance_impact=-cleared_amount,
                description=f"Invoice {invoice.invoice_number} marked {new_status}",
                posted_at=timezone.now(),
            )
            apply_balance_impact(invoice.subscriber_id, -cleared_amount)
            touch_billing_account(invoice.subscriber_id, outstanding_delta=-cleared_amount)
            invoice.balance_due = Decimal("0")
        invoice.status = new_status

    invoice.save()
    return invoice


def generate_due_invoices(run_date=None, subscriber_ids=None) -> dict:
    settings_obj = get_settings()
    run_date = run_date or timezone.now()

    period_start = run_date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if period_start.month == 12:
        next_month_start = period_start.replace(year=period_start.year + 1, month=1)
    else:
        next_month_start = period_start.replace(month=period_start.month + 1)
    period_end = next_month_start - timedelta(microseconds=1)

    sync_billing_accounts(subscriber_ids)

    accounts_qs = BillingAccount.objects.select_related("subscriber").filter(
        auto_generate_invoices=True,
        status__in=[BillingAccount.Status.ACTIVE, BillingAccount.Status.SUSPENDED],
    )
    if subscriber_ids:
        accounts_qs = accounts_qs.filter(subscriber_id__in=subscriber_ids)

    generated_invoices = []
    for account in accounts_qs:
        already_exists = (
            Invoice.objects.filter(
                subscriber=account.subscriber,
                billing_period_start=period_start,
                billing_period_end=period_end,
            )
            .exclude(status__in=[Invoice.Status.VOID, Invoice.Status.CANCELLED])
            .exists()
        )
        if already_exists:
            continue

        due_date = period_end + timedelta(days=account.due_days)
        invoice = generate_invoice(
            {
                "subscriber_id": account.subscriber_id,
                "billing_period_start": period_start,
                "billing_period_end": period_end,
                "due_date": due_date,
                "notes": f"Generated by due invoice run {run_date.isoformat()}",
            }
        )
        generated_invoices.append(invoice)

    return {
        "run_date": run_date,
        "billing_period_start": period_start,
        "billing_period_end": period_end,
        "invoice_prefix": settings_obj.invoice_prefix,
        "generated_count": len(generated_invoices),
        "invoices": generated_invoices,
    }


def refresh_overdue_invoices() -> dict:
    settings_obj = get_settings()
    now = timezone.now()

    invoices = Invoice.objects.select_related("subscriber").filter(
        status__in=[Invoice.Status.ISSUED, Invoice.Status.PARTIALLY_PAID],
        balance_due__gt=0,
        due_date__lt=now,
    )

    refreshed_ids = []
    suspended_subscriber_ids = []

    for invoice in invoices:
        invoice.status = Invoice.Status.OVERDUE
        invoice.save(update_fields=["status"])
        refreshed_ids.append(invoice.id)

        if settings_obj.auto_suspend_on_overdue and settings_obj.suspension_enabled:
            suspend_at = invoice.due_date + timedelta(days=settings_obj.grace_period_days)
            if suspend_at <= now and invoice.subscriber.status == "active":
                subscriber = invoice.subscriber
                subscriber.status = "suspended"
                subscriber.suspension_reason = f"Overdue invoice {invoice.invoice_number}"
                subscriber.save(update_fields=["status", "suspension_reason"])
                suspended_subscriber_ids.append(subscriber.id)

    return {
        "refreshed_count": len(refreshed_ids),
        "updated_invoice_ids": refreshed_ids,
        "suspended_subscriber_ids": suspended_subscriber_ids,
    }


@transaction.atomic
def create_adjustment(input_data: dict) -> LedgerEntry:
    posted_at = input_data.get("posted_at") or timezone.now()

    invoice = None
    if input_data.get("invoice_id"):
        invoice = (
            Invoice.objects.select_related("subscriber", "customer")
            .filter(id=input_data["invoice_id"])
            .first()
        )

    billing_account = None
    if input_data.get("billing_account_id"):
        billing_account = BillingAccount.objects.filter(id=input_data["billing_account_id"]).first()

    subscriber_id = None
    if invoice is not None:
        subscriber_id = invoice.subscriber_id
    elif input_data.get("subscriber_id"):
        subscriber_id = input_data["subscriber_id"]
    elif billing_account is not None:
        subscriber_id = billing_account.subscriber_id

    if subscriber_id is None:
        raise AppError("Subscriber context is required for adjustment", 400)

    if billing_account is None:
        billing_account = BillingAccount.objects.filter(subscriber_id=subscriber_id).first()
        if billing_account is None:
            sync_billing_accounts([subscriber_id])
            billing_account = BillingAccount.objects.filter(subscriber_id=subscriber_id).first()
        if billing_account is None:
            raise AppError("Billing account not found", 404)

    subscriber = Subscriber.objects.select_related("customer").get(id=subscriber_id)
    amount = round2(Decimal(str(input_data["amount"])))
    adjustment_type = input_data["adjustment_type"]

    if invoice is not None and adjustment_type == "credit" and amount > invoice.balance_due:
        raise AppError("Credit amount exceeds invoice balance due", 400)

    balance_impact = amount if adjustment_type == "debit" else -amount

    if invoice is not None:
        invoice.balance_due = max(Decimal("0"), round2(invoice.balance_due + balance_impact))
        if invoice.balance_due == 0 and invoice.status not in (
            Invoice.Status.VOID,
            Invoice.Status.CANCELLED,
        ):
            invoice.status = Invoice.Status.PAID
        elif invoice.balance_due > 0 and invoice.status == Invoice.Status.PAID:
            invoice.status = Invoice.Status.ISSUED
        invoice.save()

    description = input_data["description"]
    if input_data.get("notes"):
        description = f"{description} - {input_data['notes']}"

    ledger_entry = LedgerEntry.objects.create(
        customer=subscriber.customer,
        subscriber=subscriber,
        invoice=invoice,
        entry_type=LedgerEntry.EntryType.ADJUSTMENT,
        debit=amount if adjustment_type == "debit" else Decimal("0"),
        credit=amount if adjustment_type == "credit" else Decimal("0"),
        balance_impact=balance_impact,
        description=description,
        posted_at=posted_at,
    )

    apply_balance_impact(subscriber_id, balance_impact)
    touch_billing_account(subscriber_id, outstanding_delta=balance_impact)

    return ledger_entry


def list_invoices():
    return (
        Invoice.objects.select_related("customer", "subscriber", "plan")
        .filter(deleted_at__isnull=True)
        .order_by("-created_at")
    )


def get_invoice_by_id(invoice_id) -> Invoice:
    invoice = (
        Invoice.objects.select_related("customer", "subscriber", "plan")
        .filter(id=invoice_id, deleted_at__isnull=True)
        .first()
    )
    if invoice is None:
        raise AppError("Invoice not found", 404)
    return invoice


def delete_invoice(invoice_id) -> None:
    invoice = get_invoice_by_id(invoice_id)
    invoice.deleted_at = timezone.now()
    invoice.save(update_fields=["deleted_at"])


def list_ledger():
    return (
        LedgerEntry.objects.select_related("customer", "subscriber", "invoice", "payment")
        .filter(deleted_at__isnull=True)
        .order_by("-posted_at")
    )


def delete_ledger_entry(entry_id) -> None:
    entry = LedgerEntry.objects.filter(id=entry_id, deleted_at__isnull=True).first()
    if entry is None:
        raise AppError("Ledger entry not found", 404)
    entry.deleted_at = timezone.now()
    entry.save(update_fields=["deleted_at"])
