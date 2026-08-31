"""End-to-end provisioning: zero-touch NEW_CONNECTION activation, validation
failures, and the suspension/reactivation/termination lifecycle."""
import uuid

import pytest

from app.models import Order, ServiceSubscription
from app.services.activation import ProvisioningService
from app.services.order_service import OrderService


def _ps(session) -> ProvisioningService:
    return ProvisioningService(session)


def _new_connection(session, tenant_id, **overrides) -> Order:
    payload = {
        "order_type": "NEW_CONNECTION",
        "customer_id": "cust-valid-001",
        "service_location_id": "loc-1",
        "requested_plan_reference": "plan-fiber-100",
        "requested_snapshot": {"ont_serial": "ONT-SN-1001", "nas_reference": "nas-test", "pop": "pop-1", "node": "node-1"},
    }
    payload.update(overrides)
    return OrderService(session).create_order(tenant_id, **payload)


def _activate(session, ps, order) -> tuple:
    ps.order_service.submit(order.id)
    state = ps.validate_order(order.id)
    assert state == "READY_FOR_FULFILMENT"
    order, saga, _ = ps.start_workflow(order.id)
    state = ps.run_to_completion(saga.id)
    session.commit()
    assert state == "COMPLETED"
    return session.get(Order, order.id), saga


def test_zero_touch_new_connection_full_flow(session, tenant_id, seeded_resources):
    ps = _ps(session)
    order = _new_connection(session, tenant_id)
    session.commit()
    order, _saga = _activate(session, ps, order)

    assert order.state == "COMPLETED"
    assert order.completed_at is not None
    sub = session.query(ServiceSubscription).filter(ServiceSubscription.order_reference == order.order_number).one()
    assert sub.status == "ACTIVE"
    assert sub.aaa_subscriber_reference is not None
    assert sub.nas_reference == "nas-test"
    assert sub.resource_references["ip_address"]
    assert sub.resource_references["port_reference"]
    # Outbox has activation + service events for downstream consumers.
    from app.models import OutboxEvent

    types = [e.event_type for e in session.query(OutboxEvent).all()]
    assert "oss.service.activated.v1" in types
    assert "oss.order.completed.v1" in types
    # Order event stream is complete and replayed cleanly.
    events = OrderService(session).events(order.id)
    assert events[-1].aggregate_version == order.aggregate_version


def test_validation_failure_blocks_activation(session, tenant_id, seeded_resources):
    ps = _ps(session)
    order = _new_connection(session, tenant_id, customer_id="cust-kyc-pending")
    session.commit()
    ps.order_service.submit(order.id)
    state = ps.validate_order(order.id)
    session.commit()
    assert state == "VALIDATION_FAILED"
    order = session.get(Order, order.id)
    assert order.state == "VALIDATION_FAILED"
    assert "kyc" in (order.failure_reason or "")
    # No saga started; retryable after KYC completes.
    from app.models import SagaInstance

    assert session.query(SagaInstance).filter(SagaInstance.order_id == order.id).count() == 0


def test_suspension_reactivation_termination_lifecycle(session, tenant_id, seeded_resources):
    ps = _ps(session)
    order = _new_connection(session, tenant_id)
    session.commit()
    order, _ = _activate(session, ps, order)
    sub = session.query(ServiceSubscription).filter(ServiceSubscription.order_reference == order.order_number).one()

    def run_operation(order_type: str) -> Order:
        op_order = OrderService(session).create_order(
            tenant_id,
            order_type=order_type,
            customer_id="cust-valid-001",
            service_location_id="loc-1",
            requested_snapshot={"ont_serial": "ONT-SN-1001", "nas_reference": "nas-test"},
        )
        op_order.service_subscription_id = sub.id
        session.commit()
        ps.order_service.submit(op_order.id)
        op_order, saga, _ = ps.start_workflow(op_order.id)
        state = ps.run_to_completion(saga.id)
        session.commit()
        assert state == "COMPLETED"
        return session.get(Order, op_order.id)

    # Suspend
    run_operation("SERVICE_SUSPENSION")
    sub = session.get(ServiceSubscription, sub.id)
    assert sub.status == "SUSPENDED"
    assert sub.suspension_date is not None
    # Reactivate
    run_operation("SERVICE_REACTIVATION")
    sub = session.get(ServiceSubscription, sub.id)
    assert sub.status == "ACTIVE"
    assert sub.suspension_date is None
    # Terminate
    run_operation("SERVICE_TERMINATION")
    sub = session.get(ServiceSubscription, sub.id)
    assert sub.status == "TERMINATED"
    assert sub.termination_date is not None


def test_draft_order_cancellation(session, tenant_id):
    ps = _ps(session)
    order = _new_connection(session, tenant_id)
    session.commit()
    ps.order_service.cancel(order.id, reason="customer changed mind")
    session.commit()
    order = session.get(Order, order.id)
    assert order.state == "CANCELLED"
    assert order.aggregate_version == 2
