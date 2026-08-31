import logging

from .models import Order
from .orchestration import run_provisioning_saga

logger = logging.getLogger("orders")


def maybe_activate_from_invoice(invoice) -> None:
    """Called from payments.services.record_payment right after an invoice is
    fully paid. If a pending Order is gated on this invoice, activates it
    automatically - this is the zero-touch provisioning trigger (Sub-milestone
    2.4). Partially-paid invoices don't trigger activation.

    record_payment runs inside its own @transaction.atomic block, so a
    provisioning failure here must NOT propagate - that would roll back the
    payment that was already successfully received. run_provisioning_saga
    already leaves its own audit trail (the order lands in `failed` with the
    full step/compensation history in OrderEvent) for ops to retry via
    POST /orders/{id}/activate once the underlying issue is fixed.
    """
    if invoice.status != invoice.Status.PAID:
        return

    order = Order.objects.filter(trigger_invoice=invoice, status=Order.Status.PENDING).first()
    if order is None:
        return

    try:
        run_provisioning_saga(order)
    except Exception:
        logger.exception(
            "Automatic activation failed for order %s triggered by invoice %s",
            order.order_number,
            invoice.invoice_number,
        )
