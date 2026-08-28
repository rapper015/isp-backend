"""Customer lifecycle service. Transitions must go through the state machine;
this is the single place lifecycle changes are made, audited and published."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Customer, CustomerLifecycleEvent
from ..state_machine import lifecycle_transition
from .audit_service import audit, correlation, outbox, timeline


def transition_customer(session: Session, tenant_id, customer_id, to_state: str, trigger: str | None = None, actor: str | None = None, reason: str | None = None, related_external_type: str | None = None, related_external_id: str | None = None) -> Customer:
    """Move a customer through the lifecycle state machine with full audit."""
    customer = session.scalar(select(Customer).where(Customer.id == customer_id, Customer.tenant_id == tenant_id))
    if customer is None:
        raise ValueError("customer not found")
    to_state = to_state.upper()
    from_state = customer.lifecycle_state
    customer.lifecycle_state = lifecycle_transition(from_state, to_state)

    event = CustomerLifecycleEvent(
        tenant_id=tenant_id, customer_id=customer.id, from_state=from_state, to_state=to_state,
        trigger=trigger, actor=actor, reason=reason, correlation_id=correlation(None),
        related_external_type=related_external_type, related_external_id=related_external_id,
    )
    session.add(event)
    request_id = correlation(None)
    audit(session, tenant_id, actor or "system", "crm.customer.lifecycle_changed", "customer", customer.id, safe_before={"lifecycle_state": from_state}, safe_after={"lifecycle_state": to_state}, reason=reason, correlation_id=request_id)
    outbox(session, "crm.customer.lifecycle_changed.v1", tenant_id, request_id, {"customer_id": str(customer.id), "from_state": from_state, "to_state": to_state, "trigger": trigger})
    timeline(session, tenant_id, "LIFECYCLE", f"Lifecycle {from_state} -> {to_state}", actor=actor, customer_id=customer.id, correlation_id=request_id)
    session.flush()
    return customer
