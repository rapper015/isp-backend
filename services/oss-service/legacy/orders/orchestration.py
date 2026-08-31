from django.utils import timezone

from aaa.exceptions import AppError
from resources.allocation import allocate_ip_address, get_active_vlan, release_ip_address
from resources.models import IPAddress
from subscribers.models import Subscriber

from .models import Order, OrderEvent
from .transitions import apply_order_transition

Status = Order.Status


class Step:
    def __init__(self, name, run, compensate=None):
        self.name = name
        self.run = run
        self.compensate = compensate


def _confirm_activation(ctx):
    """Re-checks the exact eligibility predicate aaa/services.authenticate() uses,
    so a completed order is guaranteed to authenticate against RADIUS with no
    separate config-push step - aaa/services.py already reads these fields live.
    """
    order = ctx["order"]
    subscriber = order.subscriber
    plan = order.plan
    is_expired = bool(subscriber.expires_at) and subscriber.expires_at < timezone.now()
    eligible = (
        order.customer.status == "active"
        and subscriber.status == "active"
        and plan.status == "active"
        and not is_expired
    )
    if not eligible:
        raise AppError("Subscriber failed the final activation eligibility check", 400)
    return {"eligible": True}


# ---------------------------------------------------------------------------
# new_service
# ---------------------------------------------------------------------------


def _validate_new_service(ctx):
    order = ctx["order"]
    if order.customer.status != "active":
        raise AppError("Customer is not active", 400)
    if order.plan is None or order.plan.status != "active":
        raise AppError("Plan is not active", 400)
    missing = [
        name
        for name, value in (
            ("subscriberUsername", order.subscriber_username),
            ("subscriberPassword", order.subscriber_password_hash),
            ("serviceType", order.service_type),
            ("installationAddress", order.installation_address),
        )
        if not value
    ]
    if missing:
        raise AppError("Order is missing required fields", 400, {"missingFields": missing})
    if Subscriber.objects.filter(username=order.subscriber_username).exists():
        raise AppError(f"Username '{order.subscriber_username}' is already taken", 400)
    return {}


def _allocate_ip(ctx):
    order = ctx["order"]
    if not order.plan.ip_pool:
        return {}
    ip_address = allocate_ip_address(order.plan.ip_pool, order=order, subscriber=order.subscriber)
    ctx["ip_address"] = ip_address
    return {"ipAddress": ip_address.address, "pool": order.plan.ip_pool}


def _compensate_allocate_ip(ctx):
    ip_address = ctx.get("ip_address")
    if ip_address is not None:
        release_ip_address(ip_address)


def _assign_vlan(ctx):
    order = ctx["order"]
    if not order.plan.vlan:
        return {}
    vlan = get_active_vlan(order.plan.vlan)
    ctx["vlan"] = vlan
    return {"vlanId": vlan.vlan_id}


def _create_subscriber(ctx):
    order = ctx["order"]
    ip_address = ctx.get("ip_address")
    subscriber = Subscriber.objects.create(
        subscriber_code=f"SUB-{order.order_number}",
        customer=order.customer,
        plan=order.plan,
        username=order.subscriber_username,
        password_hash=order.subscriber_password_hash,
        service_type=order.service_type,
        installation_address=order.installation_address,
        mac_address=order.mac_address,
        static_ip_address=ip_address.address if ip_address else "",
        status=Subscriber.Status.ACTIVE,
    )
    if ip_address is not None:
        ip_address.allocated_subscriber = subscriber
        ip_address.save(update_fields=["allocated_subscriber"])
    order.subscriber = subscriber
    order.save(update_fields=["subscriber", "updated_at"])
    ctx["subscriber"] = subscriber
    return {"subscriberId": subscriber.id, "subscriberCode": subscriber.subscriber_code}


def _compensate_create_subscriber(ctx):
    subscriber = ctx.get("subscriber")
    if subscriber is None:
        return
    subscriber.deleted_at = timezone.now()
    subscriber.save(update_fields=["deleted_at"])
    order = ctx["order"]
    order.subscriber = None
    order.save(update_fields=["subscriber", "updated_at"])


NEW_SERVICE_STEPS = [
    Step("validate", _validate_new_service),
    Step("allocate_ip", _allocate_ip, _compensate_allocate_ip),
    Step("assign_vlan", _assign_vlan),
    Step("create_subscriber", _create_subscriber, _compensate_create_subscriber),
    Step("confirm_activation", _confirm_activation),
]


# ---------------------------------------------------------------------------
# upgrade / downgrade
# ---------------------------------------------------------------------------


def _validate_change(ctx):
    order = ctx["order"]
    if order.subscriber is None:
        raise AppError("Order is missing the subscriber to modify", 400)
    if order.subscriber.status != "active":
        raise AppError("Subscriber is not active", 400)
    if order.plan is None or order.plan.status != "active":
        raise AppError("Target plan is not active", 400)
    return {}


def _reassign_vlan(ctx):
    order = ctx["order"]
    if not order.plan.vlan:
        return {}
    vlan = get_active_vlan(order.plan.vlan)
    ctx["vlan"] = vlan
    return {"vlanId": vlan.vlan_id}


def _update_subscriber_plan(ctx):
    order = ctx["order"]
    subscriber = order.subscriber
    ctx["previous_plan_id"] = subscriber.plan_id
    subscriber.plan = order.plan
    subscriber.save(update_fields=["plan"])
    return {"planId": order.plan_id}


def _compensate_update_subscriber_plan(ctx):
    subscriber = ctx["order"].subscriber
    subscriber.plan_id = ctx["previous_plan_id"]
    subscriber.save(update_fields=["plan"])


PLAN_CHANGE_STEPS = [
    Step("validate", _validate_change),
    Step("reassign_vlan", _reassign_vlan),
    Step("update_subscriber_plan", _update_subscriber_plan, _compensate_update_subscriber_plan),
    Step("confirm_activation", _confirm_activation),
]


# ---------------------------------------------------------------------------
# disconnect
# ---------------------------------------------------------------------------


def _validate_disconnect(ctx):
    order = ctx["order"]
    if order.subscriber is None:
        raise AppError("Order is missing the subscriber to disconnect", 400)
    if order.subscriber.status == Subscriber.Status.TERMINATED:
        raise AppError("Subscriber is already terminated", 400)
    return {}


def _release_ip(ctx):
    subscriber = ctx["order"].subscriber
    ip_address = IPAddress.objects.filter(
        allocated_subscriber=subscriber, status=IPAddress.Status.ALLOCATED
    ).first()
    if ip_address is None:
        return {}
    release_ip_address(ip_address)
    ctx["released_ip"] = ip_address
    return {"ipAddress": ip_address.address}


def _compensate_release_ip(ctx):
    ip_address = ctx.get("released_ip")
    if ip_address is None:
        return
    ip_address.refresh_from_db()
    if ip_address.status != IPAddress.Status.AVAILABLE:
        return
    ip_address.status = IPAddress.Status.ALLOCATED
    ip_address.allocated_subscriber = ctx["order"].subscriber
    ip_address.allocated_order = ctx["order"]
    ip_address.allocated_at = timezone.now()
    ip_address.save(
        update_fields=["status", "allocated_subscriber", "allocated_order", "allocated_at"]
    )


def _terminate_subscriber(ctx):
    subscriber = ctx["order"].subscriber
    ctx["previous_status"] = subscriber.status
    subscriber.status = Subscriber.Status.TERMINATED
    subscriber.save(update_fields=["status"])
    return {"subscriberStatus": subscriber.status}


def _compensate_terminate_subscriber(ctx):
    subscriber = ctx["order"].subscriber
    subscriber.status = ctx["previous_status"]
    subscriber.save(update_fields=["status"])


DISCONNECT_STEPS = [
    Step("validate", _validate_disconnect),
    Step("release_ip", _release_ip, _compensate_release_ip),
    Step("terminate_subscriber", _terminate_subscriber, _compensate_terminate_subscriber),
]


STEP_PIPELINES = {
    Order.OrderType.NEW_SERVICE: NEW_SERVICE_STEPS,
    Order.OrderType.UPGRADE: PLAN_CHANGE_STEPS,
    Order.OrderType.DOWNGRADE: PLAN_CHANGE_STEPS,
    Order.OrderType.DISCONNECT: DISCONNECT_STEPS,
}


def _compensate(order, completed_steps, ctx, admin_id):
    for step in reversed(completed_steps):
        if step.compensate is None:
            continue
        step.compensate(ctx)
        OrderEvent.objects.create(
            order=order,
            event_type=OrderEvent.EventType.STEP_COMPENSATED,
            step_name=step.name,
            performed_by_id=admin_id,
        )


def run_provisioning_saga(order: Order, admin_id=None) -> Order:
    """Runs order.order_type's step pipeline synchronously, in-process. Each
    step's own DB writes commit as they happen (no single wrapping
    transaction) - on failure, already-completed steps are undone via their
    explicit compensate() function rather than a DB rollback, which is what
    makes this a saga rather than a single atomic operation. Every step
    start/success/failure/compensation is written as an OrderEvent.
    """
    if order.status not in (Status.PENDING, Status.FAILED):
        raise AppError(
            f"Order cannot be activated from status {order.status}",
            400,
            {"allowedStatuses": [Status.PENDING, Status.FAILED]},
        )

    steps = STEP_PIPELINES.get(order.order_type)
    if not steps:
        raise AppError(f"No provisioning pipeline defined for order type {order.order_type}", 400)

    apply_order_transition(order, Status.PROCESSING, admin_id)

    ctx = {"order": order}
    completed_steps = []

    for step in steps:
        OrderEvent.objects.create(
            order=order,
            event_type=OrderEvent.EventType.STEP_STARTED,
            step_name=step.name,
            performed_by_id=admin_id,
        )
        try:
            detail = step.run(ctx) or {}
        except Exception as exc:
            message = exc.message if isinstance(exc, AppError) else str(exc)
            OrderEvent.objects.create(
                order=order,
                event_type=OrderEvent.EventType.STEP_FAILED,
                step_name=step.name,
                performed_by_id=admin_id,
                detail={"error": message},
            )
            _compensate(order, completed_steps, ctx, admin_id)
            apply_order_transition(order, Status.FAILED, admin_id, reason=message)
            raise
        else:
            OrderEvent.objects.create(
                order=order,
                event_type=OrderEvent.EventType.STEP_COMPLETED,
                step_name=step.name,
                performed_by_id=admin_id,
                detail=detail,
            )
            completed_steps.append(step)

    apply_order_transition(order, Status.COMPLETED, admin_id)
    return order
