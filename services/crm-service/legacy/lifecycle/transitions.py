from aaa.exceptions import AppError
from customers.models import Customer
from kyc.services import has_verified_kyc

from .models import CustomerLifecycleEvent

Status = Customer.Status

# Deterministic state model - only these from -> to moves are allowed.
# TERMINATED is terminal (no outbound transitions).
ALLOWED_TRANSITIONS = {
    Status.ONBOARDING: {Status.ACTIVE, Status.INACTIVE, Status.TERMINATED},
    Status.ACTIVE: {Status.SUSPENDED, Status.INACTIVE, Status.TERMINATED},
    Status.INACTIVE: {Status.ONBOARDING, Status.ACTIVE, Status.TERMINATED},
    Status.SUSPENDED: {Status.ACTIVE, Status.TERMINATED},
    Status.TERMINATED: set(),
}


def apply_transition(
    customer: Customer, to_status: str, admin_id, reason: str = ""
) -> Customer:
    """Moves a customer to `to_status`, enforcing the state model and logging
    an auditable CustomerLifecycleEvent. This is the single place customer
    status changes - both the /customers PATCH endpoint and the dedicated
    /lifecycle/transition endpoint route through it, so the same rules
    (allowed transitions, verified-KYC-before-activation) apply everywhere.
    """
    valid_statuses = {choice for choice, _ in Customer.Status.choices}
    if to_status not in valid_statuses:
        raise AppError("Validation failed", 400, {"invalidStatus": to_status})

    from_status = customer.status
    if to_status == from_status:
        raise AppError(f"Customer is already {to_status}", 400)

    allowed = ALLOWED_TRANSITIONS.get(from_status, set())
    if to_status not in allowed:
        raise AppError(
            f"Cannot transition customer from {from_status} to {to_status}",
            400,
            {"allowedTransitions": sorted(allowed)},
        )

    if to_status == Status.ACTIVE and not has_verified_kyc(customer.id):
        raise AppError(
            "Customer cannot be activated without at least one verified KYC document",
            400,
        )

    customer.status = to_status
    customer.save(update_fields=["status", "updated_at"])

    return CustomerLifecycleEvent.objects.create(
        customer=customer,
        from_status=from_status,
        to_status=to_status,
        reason=reason,
        performed_by_id=admin_id,
    ).customer
