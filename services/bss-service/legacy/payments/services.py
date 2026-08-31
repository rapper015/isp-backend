from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from aaa.exceptions import AppError
from billing.models import Invoice, LedgerEntry
from billing.sequences import next_payment_reference
from billing.services import round2
from orders.activation import maybe_activate_from_invoice

from .models import Payment


def list_payments():
    return Payment.objects.select_related("invoice", "customer", "subscriber").filter(
        deleted_at__isnull=True
    ).order_by("-received_at")


def get_payment(payment_id) -> Payment:
    payment = (
        Payment.objects.select_related("invoice", "customer", "subscriber")
        .filter(id=payment_id, deleted_at__isnull=True)
        .first()
    )
    if payment is None:
        raise AppError("Payment not found", 404)
    return payment


def delete_payment(payment_id) -> None:
    payment = get_payment(payment_id)
    payment.deleted_at = timezone.now()
    payment.save(update_fields=["deleted_at"])


@transaction.atomic
def record_payment(input_data: dict) -> Payment:
    invoice = (
        Invoice.objects.select_related("subscriber", "customer")
        .filter(id=input_data["invoice_id"])
        .first()
    )
    if invoice is None:
        raise AppError("Invoice not found", 404)

    amount = round2(Decimal(str(input_data["amount"])))
    if amount > invoice.balance_due:
        raise AppError("Payment amount exceeds outstanding balance", 400)

    received_at = input_data.get("received_at") or timezone.now()

    payment = Payment.objects.create(
        payment_reference=next_payment_reference(),
        invoice=invoice,
        customer=invoice.customer,
        subscriber=invoice.subscriber,
        amount=amount,
        method=input_data["method"],
        received_at=received_at,
        notes=input_data.get("notes") or "",
    )

    invoice.balance_due = round2(invoice.balance_due - amount)
    invoice.status = (
        Invoice.Status.PAID if invoice.balance_due == 0 else Invoice.Status.PARTIALLY_PAID
    )
    invoice.save(update_fields=["balance_due", "status"])

    LedgerEntry.objects.create(
        customer=invoice.customer,
        subscriber=invoice.subscriber,
        invoice=invoice,
        payment=payment,
        entry_type=LedgerEntry.EntryType.PAYMENT,
        debit=Decimal("0"),
        credit=amount,
        balance_impact=-amount,
        description=f"Payment {payment.payment_reference} received",
        posted_at=received_at,
    )

    # Note: intentionally does NOT touch BillingAccount.outstanding_balance
    # here, matching the Node reference — the billing account is a derived
    # cache that only gets re-synced from subscriber.current_balance later.
    subscriber = invoice.subscriber
    subscriber.current_balance = max(Decimal("0"), round2(subscriber.current_balance - amount))
    subscriber.save(update_fields=["current_balance"])

    maybe_activate_from_invoice(invoice)

    return payment
