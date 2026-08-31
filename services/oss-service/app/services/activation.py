"""High-level provisioning facade: validates an order, starts the appropriate
workflow saga and drives it. Exposes the zero-touch activation entry point."""
from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from ..models import Order, SagaInstance
from .order_service import OrderService
from .provisioning import ORDER_TYPE_TO_WORKFLOW, SAGA_BUILDERS, validate_and_prepare
from .saga_engine import SagaEngine


class ProvisioningService:
    def __init__(self, session: Session):
        self.session = session
        self.order_service = OrderService(session)
        self.engine = SagaEngine(session, order_service=self.order_service)
        for workflow_type, builder in SAGA_BUILDERS.items():
            self.engine.register(builder())

    # -- entry points -------------------------------------------------------
    def validate_order(self, order_id: uuid.UUID) -> str:
        """Runs the pre-saga validation phase for an order."""
        order = self.order_service.repo.load(order_id)
        if order.state not in ("SUBMITTED", "VALIDATING", "VALIDATION_FAILED"):
            raise ValueError(f"order in state {order.state} cannot be validated")
        return validate_and_prepare(self.session, self.order_service, order)

    def start_workflow(self, order_id: uuid.UUID, correlation_id: str | None = None) -> tuple[Order, SagaInstance | None, str]:
        """Starts the saga for an order's operation type. For NEW_CONNECTION the
        validation phase runs first; PAYMENT_PENDING returns without a saga."""
        order = self.order_service.repo.load(order_id)
        workflow = ORDER_TYPE_TO_WORKFLOW.get(order.order_type)
        if workflow is None:
            raise ValueError(f"no workflow defined for order type {order.order_type!r}")
        if workflow == "NEW_CONNECTION":
            state = validate_and_prepare(self.session, self.order_service, order)
            if state in ("VALIDATION_FAILED", "PAYMENT_PENDING"):
                return order, None, state
        else:
            # Service operations reference an existing subscription; a light
            # validation advance (SUBMITTED -> VALIDATING -> READY) suffices.
            if order.state == "SUBMITTED":
                self.order_service.validate(order.id)
            if order.state == "VALIDATING":
                self.order_service.transition(order.id, "READY_FOR_FULFILMENT", reason="workflow ready")
        if order.state != "READY_FOR_FULFILMENT":
            raise ValueError(f"order in state {order.state} is not ready for fulfilment")
        saga = self.engine.start(order.tenant_id, order.id, workflow, correlation_id or str(uuid.uuid4()))
        self.engine.advance(saga.id)
        self.session.commit()
        return order, saga, "RUNNING"

    def advance(self, order_id: uuid.UUID | None = None, saga_id: uuid.UUID | None = None) -> str:
        if saga_id is not None:
            return self.engine.advance(saga_id)
        if order_id is not None:
            order = self.order_service.repo.load(order_id)
            saga = self.session.query(SagaInstance).filter(SagaInstance.order_id == order.id).order_by(SagaInstance.created_at.desc()).first()
            if saga is None:
                raise RuntimeError(f"no saga for order {order_id}")
            return self.engine.advance(saga.id)
        raise ValueError("order_id or saga_id required")

    def run_to_completion(self, saga_id: uuid.UUID, max_passes: int = 200) -> str:
        state = ""
        for _ in range(max_passes):
            state = self.engine.advance(saga_id)
            if state in ("COMPLETED", "COMPENSATED", "FAILED", "CANCELLED", "MANUAL_INTERVENTION"):
                break
        self.session.commit()
        return state

    def resume(self, saga_id: uuid.UUID, resolved_by: str = "operator") -> str:
        return self.engine.resume(saga_id, resolved_by=resolved_by)

    def compensate(self, saga_id: uuid.UUID, reason: str | None = None) -> str:
        return self.engine.compensate(saga_id, reason=reason)
