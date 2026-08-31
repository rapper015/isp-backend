"""Order command service: create/submit/validate/transition/cancel/retry/resume/
compensate with validated state machine, event sourcing, outbox publication and
command idempotency."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..enums import ORDER_COMMANDS, ORDER_SOURCES, ORDER_STATES, ORDER_TYPES
from ..events import publish_outbox
from ..models import Order, OrderCommand, OrderStatusHistory
from ..state_machine import ORDER_TRANSITIONS, order_transition, order_terminal
from .order_repository import ConcurrencyConflict, OrderNotFound, OrderRepository


def _now() -> datetime:
    return datetime.now(timezone.utc)


def next_order_number(prefix: str = "ORD") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12].upper()}"


def valid_actions(state: str) -> list[str]:
    return sorted(ORDER_TRANSITIONS[state])


class OrderService:
    def __init__(self, session: Session):
        self.session = session
        self.repo = OrderRepository(session)

    # -- construction -------------------------------------------------------
    def create_order(
        self,
        tenant_id: uuid.UUID,
        *,
        order_type: str,
        customer_id: str | None = None,
        service_location_id: str | None = None,
        requested_plan_reference: str | None = None,
        previous_plan_reference: str | None = None,
        requested_activation_date: datetime | None = None,
        priority: str = "MEDIUM",
        source_channel: str = "API",
        franchise_id: str | None = None,
        reseller_id: str | None = None,
        requested_snapshot: dict | None = None,
        actor: str = "system",
        correlation_id: str | None = None,
    ) -> Order:
        if order_type not in ORDER_TYPES:
            raise ValueError(f"invalid order type {order_type!r}")
        if priority not in ("LOW", "MEDIUM", "HIGH", "URGENT"):
            raise ValueError(f"invalid priority {priority!r}")
        if source_channel not in ORDER_SOURCES:
            raise ValueError(f"invalid source channel {source_channel!r}")

        def _s(value):
            return str(value) if value is not None else None

        order = Order(
            tenant_id=tenant_id,
            order_number=next_order_number(),
            order_type=order_type,
            state="DRAFT",
            customer_id=_s(customer_id),
            service_location_id=_s(service_location_id),
            requested_plan_reference=requested_plan_reference,
            previous_plan_reference=previous_plan_reference,
            requested_activation_date=requested_activation_date,
            priority=priority,
            source_channel=source_channel,
            franchise_id=_s(franchise_id),
            reseller_id=_s(reseller_id),
            requested_snapshot=requested_snapshot or {},
            created_by=actor,
        )
        self.session.add(order)
        self.session.flush()
        self.repo.append(
            order,
            "oss.order.created.v1",
            {
                "order_number": order.order_number,
                "order_type": order.order_type,
                "customer_id": str(customer_id) if customer_id else None,
                "requested_plan_reference": requested_plan_reference,
                "priority": priority,
                "source_channel": source_channel,
            },
            actor_type="user" if actor != "system" else "system",
            actor_id=actor,
            correlation_id=correlation_id,
        )
        self._record_history(order, "DRAFT", "DRAFT", actor, "order created", correlation_id)
        publish_outbox(self.session, "oss.order.created.v1", {"order_id": str(order.id), "order_number": order.order_number}, tenant_id, correlation_id)
        self.session.flush()
        return order

    # -- transitions --------------------------------------------------------
    def transition(
        self,
        order_id: uuid.UUID,
        target: str,
        *,
        reason: str | None = None,
        actor: str = "system",
        correlation_id: str | None = None,
        event_type: str | None = None,
        payload: dict | None = None,
        outbox_type: str | None = None,
        commit: bool = True,
    ) -> Order:
        order = self.repo.load(order_id)
        current = order.state
        order_transition(current, target)  # raises ValueError if invalid
        outbox_type = outbox_type or {
            "SUBMITTED": "oss.order.submitted.v1",
            "CANCELLATION_REQUESTED": "oss.order.cancellation_requested.v1",
            "CANCELLED": "oss.order.cancelled.v1",
            "COMPLETED": "oss.order.completed.v1",
            "FAILED": "oss.order.failed.v1",
            "ROLLED_BACK": "oss.order.rolled_back.v1",
            "COMPENSATING": "oss.order.compensation_started.v1",
            "MANUAL_INTERVENTION_REQUIRED": "oss.order.manual_intervention_required.v1",
            "VALIDATION_FAILED": "oss.order.validation_failed.v1",
            "PAYMENT_PENDING": "oss.order.payment_pending.v1",
            "READY_FOR_FULFILMENT": "oss.order.ready_for_fulfilment.v1",
            "RESOURCE_RESERVATION": "oss.order.resources_reserved.v1",
            "PROVISIONING": "oss.order.provisioning_started.v1",
            "VERIFYING": "oss.order.verification_started.v1",
        }.get(target, "oss.order.state_changed.v1")
        event_type = event_type or outbox_type
        self.repo.append(
            order,
            event_type,
            payload or {"from_state": current, "to_state": target, "reason": reason},
            actor_type="user" if actor != "system" else "system",
            actor_id=actor,
            correlation_id=correlation_id,
            expected_version=order.aggregate_version,
        )
        order.state = target
        if target == "COMPLETED":
            order.completed_at = _now()
        if target in ("FAILED", "MANUAL_INTERVENTION_REQUIRED", "VALIDATION_FAILED") and reason:
            order.failure_reason = reason
        self._record_history(order, current, target, actor, reason, correlation_id)
        publish_outbox(self.session, outbox_type, {"order_id": str(order.id), "order_number": order.order_number, "from_state": current, "to_state": target, "reason": reason}, order.tenant_id, correlation_id)
        if commit:
            self.session.commit()
        return order

    # -- idempotent command helper ------------------------------------------
    def run_command(
        self,
        order_id: uuid.UUID,
        command: str,
        *,
        idempotency_key: str,
        correlation_id: str,
        fn,
        actor: str = "system",
    ) -> tuple[dict, bool]:
        """Executes fn(order) once per (tenant, idempotency_key). Returns
        (result, already_processed)."""
        order = self.repo.load(order_id)
        existing = self.session.scalar(
            select(OrderCommand).where(
                OrderCommand.tenant_id == order.tenant_id,
                OrderCommand.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            return existing.result, True
        command_row = OrderCommand(
            tenant_id=order.tenant_id,
            order_id=order.id,
            command=command,
            status="QUEUED",
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )
        self.session.add(command_row)
        result = fn(order)
        command_row.status = "DONE"
        command_row.result = result if isinstance(result, dict) else {"ok": bool(result)}
        self.session.commit()
        return command_row.result, False

    # -- convenience commands -----------------------------------------------
    def submit(self, order_id, *, actor="system", idempotency_key=None, correlation_id=None) -> Order:
        correlation_id = correlation_id or str(uuid.uuid4())
        idempotency_key = idempotency_key or f"submit:{order_id}:{correlation_id}"
        order = self.repo.load(order_id)
        if order.state == "SUBMITTED":
            return order
        return self.transition(order_id, "SUBMITTED", actor=actor, correlation_id=correlation_id)

    def validate(self, order_id, *, actor="system", correlation_id=None) -> Order:
        return self.transition(order_id, "VALIDATING", actor=actor, correlation_id=correlation_id)

    def mark_validation_failed(self, order_id, reason, *, actor="system", correlation_id=None) -> Order:
        return self.transition(order_id, "VALIDATION_FAILED", reason=reason, actor=actor, correlation_id=correlation_id)

    def approve_payment(self, order_id, *, actor="system", correlation_id=None) -> Order:
        return self.transition(order_id, "READY_FOR_FULFILMENT", reason="payment approved", actor=actor, correlation_id=correlation_id)

    def request_cancel(self, order_id, reason="customer requested cancellation", *, actor="system", correlation_id=None) -> Order:
        return self.transition(order_id, "CANCELLATION_REQUESTED", reason=reason, actor=actor, correlation_id=correlation_id)

    def cancel(self, order_id, reason="cancelled", *, actor="system", correlation_id=None) -> Order:
        return self.transition(order_id, "CANCELLED", reason=reason, actor=actor, correlation_id=correlation_id)

    def retry(self, order_id, *, actor="system", correlation_id=None) -> Order:
        order = self.repo.load(order_id)
        target = "SUBMITTED" if order.state == "VALIDATION_FAILED" else "SUBMITTED"
        return self.transition(order_id, target, actor=actor, correlation_id=correlation_id)

    def resume(self, order_id, *, actor="system", correlation_id=None) -> Order:
        order = self.repo.load(order_id)
        if order.state not in ("MANUAL_INTERVENTION_REQUIRED", "FAILED", "CANCELLATION_REQUESTED"):
            raise ValueError(f"cannot resume order in state {order.state}")
        target = "SUBMITTED"
        return self.transition(order_id, target, actor=actor, correlation_id=correlation_id, outbox_type="oss.order.resumed.v1")

    def compensate(self, order_id, reason="compensating after failure", *, actor="system", correlation_id=None) -> Order:
        return self.transition(order_id, "COMPENSATING", reason=reason, actor=actor, correlation_id=correlation_id)

    # -- queries ------------------------------------------------------------
    def history(self, order_id: uuid.UUID) -> list[OrderStatusHistory]:
        return list(self.session.scalars(select(OrderStatusHistory).where(OrderStatusHistory.order_id == order_id).order_by(OrderStatusHistory.created_at)))

    def events(self, order_id: uuid.UUID) -> list:
        return self.repo.events(order_id)

    def ensure_state(self, order_id: uuid.UUID, target: str, actor="saga", correlation_id=None) -> Order | None:
        """Idempotent transition helper used by saga steps."""
        try:
            order = self.repo.load(order_id)
        except OrderNotFound:
            return None
        if order.state == target:
            return order
        if order_terminal(order.state):
            return order
        try:
            return self.transition(order_id, target, actor=actor, correlation_id=correlation_id, commit=False)
        except ValueError:
            return order

    # -- helpers ------------------------------------------------------------
    def _record_history(self, order: Order, from_state: str, to_state: str, actor: str, reason: str | None, correlation_id: str | None) -> None:
        self.session.add(
            OrderStatusHistory(
                tenant_id=order.tenant_id,
                order_id=order.id,
                from_state=from_state,
                to_state=to_state,
                actor=actor,
                reason=reason,
                correlation_id=correlation_id,
            )
        )
