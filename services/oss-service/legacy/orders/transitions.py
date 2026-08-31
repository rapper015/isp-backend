from aaa.exceptions import AppError

from .models import Order, OrderEvent

Status = Order.Status

# Deterministic state model - only these from -> to moves are allowed.
# COMPLETED/CANCELLED are terminal; FAILED can be retried (back to PROCESSING)
# or abandoned (CANCELLED).
ALLOWED_TRANSITIONS = {
    Status.PENDING: {Status.PROCESSING, Status.CANCELLED},
    Status.PROCESSING: {Status.COMPLETED, Status.FAILED},
    Status.FAILED: {Status.PROCESSING, Status.CANCELLED},
    Status.COMPLETED: set(),
    Status.CANCELLED: set(),
}


def apply_order_transition(
    order: Order, to_status: str, admin_id=None, reason: str = ""
) -> Order:
    """Moves an order to `to_status`, enforcing the state model and logging an
    auditable OrderEvent. This is the single place order status changes, so
    both admin-triggered actions (activate/cancel) and the saga engine
    (orders/orchestration.py) apply the same rules.
    """
    valid_statuses = {choice for choice, _ in Order.Status.choices}
    if to_status not in valid_statuses:
        raise AppError("Validation failed", 400, {"invalidStatus": to_status})

    from_status = order.status
    if to_status == from_status:
        raise AppError(f"Order is already {to_status}", 400)

    allowed = ALLOWED_TRANSITIONS.get(from_status, set())
    if to_status not in allowed:
        raise AppError(
            f"Cannot transition order from {from_status} to {to_status}",
            400,
            {"allowedTransitions": sorted(allowed)},
        )

    order.status = to_status
    order.save(update_fields=["status", "updated_at"])

    OrderEvent.objects.create(
        order=order,
        event_type=OrderEvent.EventType.STATUS_CHANGED,
        from_status=from_status,
        to_status=to_status,
        performed_by_id=admin_id,
        detail={"reason": reason} if reason else {},
    )

    return order
