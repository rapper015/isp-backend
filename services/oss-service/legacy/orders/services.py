from django.utils import timezone

from aaa.exceptions import AppError
from billing.models import Invoice
from common.passwords import hash_password
from customers.models import Customer
from plans.models import Plan
from resellers.models import Branch, Franchise
from subscribers.models import Subscriber

from .models import Order, OrderEvent
from .sequences import next_order_number


def _get(data: dict, *keys: str, default=None):
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return default


def list_orders():
    return (
        Order.objects.select_related("customer", "subscriber", "plan", "franchise", "branch")
        .filter(deleted_at__isnull=True)
        .order_by("-created_at")
    )


def get_order(order_id) -> Order:
    order = (
        Order.objects.select_related("customer", "subscriber", "plan", "franchise", "branch")
        .filter(id=order_id, deleted_at__isnull=True)
        .first()
    )
    if order is None:
        raise AppError("Order not found", 404)
    return order


def create_order(data: dict, admin_id=None) -> Order:
    order_type = _get(data, "orderType", "order_type")
    valid_types = {choice for choice, _ in Order.OrderType.choices}
    if order_type not in valid_types:
        raise AppError("Validation failed", 400, {"invalidFields": ["orderType"]})

    customer_id = _get(data, "customerId", "customer_id")
    if not customer_id:
        raise AppError("Validation failed", 400, {"missingFields": ["customerId"]})
    customer = Customer.objects.filter(id=customer_id, deleted_at__isnull=True).first()
    if customer is None:
        raise AppError("Customer not found", 400)

    subscriber_id = _get(data, "subscriberId", "subscriber_id")
    requires_subscriber = order_type != Order.OrderType.NEW_SERVICE
    if requires_subscriber and not subscriber_id:
        raise AppError("Validation failed", 400, {"missingFields": ["subscriberId"]})
    subscriber = None
    if subscriber_id:
        subscriber = Subscriber.objects.filter(id=subscriber_id, deleted_at__isnull=True).first()
        if subscriber is None:
            raise AppError("Subscriber not found", 400)

    plan_id = _get(data, "planId", "plan_id")
    requires_plan = order_type != Order.OrderType.DISCONNECT
    if requires_plan and not plan_id:
        raise AppError("Validation failed", 400, {"missingFields": ["planId"]})
    plan = None
    if plan_id:
        plan = Plan.objects.filter(id=plan_id, deleted_at__isnull=True).first()
        if plan is None:
            raise AppError("Plan not found", 400)

    franchise_id = _get(data, "franchiseId", "franchise_id")
    franchise = None
    if franchise_id:
        franchise = Franchise.objects.filter(id=franchise_id, deleted_at__isnull=True).first()
        if franchise is None:
            raise AppError("Franchise not found", 400)

    branch_id = _get(data, "branchId", "branch_id")
    branch = None
    if branch_id:
        branch = Branch.objects.filter(id=branch_id, deleted_at__isnull=True).first()
        if branch is None:
            raise AppError("Branch not found", 400)

    trigger_invoice_id = _get(data, "triggerInvoiceId", "trigger_invoice_id")
    trigger_invoice = None
    if trigger_invoice_id:
        trigger_invoice = Invoice.objects.filter(id=trigger_invoice_id).first()
        if trigger_invoice is None:
            raise AppError("Invoice not found", 400)

    subscriber_password_hash = ""
    if order_type == Order.OrderType.NEW_SERVICE:
        password = _get(data, "subscriberPassword", "subscriber_password")
        missing = [
            name
            for name, value in (
                ("subscriberUsername", _get(data, "subscriberUsername", "subscriber_username")),
                ("subscriberPassword", password),
                ("serviceType", _get(data, "serviceType", "service_type")),
                (
                    "installationAddress",
                    _get(data, "installationAddress", "installation_address"),
                ),
            )
            if not value
        ]
        if missing:
            raise AppError("Validation failed", 400, {"missingFields": missing})
        subscriber_password_hash = hash_password(password)

    order = Order.objects.create(
        order_number=next_order_number(),
        order_type=order_type,
        customer=customer,
        subscriber=subscriber,
        plan=plan,
        franchise=franchise,
        branch=branch,
        trigger_invoice=trigger_invoice,
        subscriber_username=_get(data, "subscriberUsername", "subscriber_username", default=""),
        subscriber_password_hash=subscriber_password_hash,
        service_type=_get(data, "serviceType", "service_type", default=""),
        installation_address=_get(
            data, "installationAddress", "installation_address", default=""
        ),
        mac_address=_get(data, "macAddress", "mac_address", default=""),
        requested_by_id=admin_id,
        assigned_to_id=_get(data, "assignedTo", "assigned_to"),
        notes=_get(data, "notes", default=""),
    )

    OrderEvent.objects.create(
        order=order,
        event_type=OrderEvent.EventType.STATUS_CHANGED,
        to_status=Order.Status.PENDING,
        performed_by_id=admin_id,
    )

    return order


def update_order(order_id, data: dict) -> Order:
    order = get_order(order_id)
    if order.status not in (Order.Status.PENDING, Order.Status.FAILED):
        raise AppError(f"Order cannot be edited from status {order.status}", 400)

    direct_fields = {
        "notes": ("notes",),
        "installation_address": ("installationAddress", "installation_address"),
        "mac_address": ("macAddress", "mac_address"),
    }
    for field, keys in direct_fields.items():
        value = _get(data, *keys)
        if value is not None:
            setattr(order, field, value)

    plan_id = _get(data, "planId", "plan_id")
    if plan_id is not None:
        plan = Plan.objects.filter(id=plan_id, deleted_at__isnull=True).first()
        if plan is None:
            raise AppError("Plan not found", 400)
        order.plan = plan

    assigned_to = _get(data, "assignedTo", "assigned_to")
    if assigned_to is not None:
        order.assigned_to_id = assigned_to

    order.save()
    return order


def delete_order(order_id) -> None:
    order = get_order(order_id)
    if order.status in (Order.Status.PROCESSING, Order.Status.COMPLETED):
        raise AppError(f"Order cannot be deleted from status {order.status}", 400)
    order.deleted_at = timezone.now()
    order.save(update_fields=["deleted_at"])
