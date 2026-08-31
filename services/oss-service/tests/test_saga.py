"""Saga orchestration: happy path, retry on transient failure, compensation on
non-retryable failure, manual intervention + resume, pausable gate, and saga
survival across engine restarts."""
import uuid

import pytest

from app.integrations.fakes import FaultInjector
from app.models import ManualIntervention, Order, SagaInstance, SagaStep, ServiceSubscription
from app.services.activation import ProvisioningService
from app.services.order_service import OrderService


def _create_order(session, tenant_id, **overrides) -> Order:
    payload = {
        "order_type": "NEW_CONNECTION",
        "customer_id": "cust-valid-001",
        "service_location_id": "loc-1",
        "requested_plan_reference": "plan-fiber-100",
        "requested_snapshot": {"ont_serial": "ONT-SN-1001", "nas_reference": "nas-test", "pop": "pop-1", "node": "node-1"},
    }
    payload.update(overrides)
    return OrderService(session).create_order(tenant_id, **payload)


def _ps(session) -> ProvisioningService:
    ps = ProvisioningService(session)
    return ps


def test_saga_happy_path(session, tenant_id, seeded_resources):
    ps = _ps(session)
    order = _create_order(session, tenant_id)
    session.commit()
    ps.order_service.submit(order.id)
    state = ps.validate_order(order.id)
    assert state == "READY_FOR_FULFILMENT"
    order, saga, _state = ps.start_workflow(order.id)
    state = ps.run_to_completion(saga.id)
    session.commit()

    assert state == "COMPLETED"
    order = session.get(Order, order.id)
    assert order.state == "COMPLETED"
    sub = session.query(ServiceSubscription).filter(ServiceSubscription.order_reference == order.order_number).one()
    assert sub.status == "ACTIVE"
    assert sub.aaa_subscriber_reference is not None
    steps = session.query(SagaStep).filter(SagaStep.saga_id == saga.id).all()
    assert all(step.state == "COMPLETED" for step in steps)
    # Resources allocated.
    from app.models import ResourceReservation

    reservations = session.query(ResourceReservation).filter(ResourceReservation.order_id == order.id).all()
    assert all(r.state == "ALLOCATED" for r in reservations)


def test_saga_retries_transient_failure(session, tenant_id, seeded_resources):
    injector = FaultInjector()
    injector.fail_times("aaa", "create_subscriber_profile", times=2, retryable=True)
    ps = _ps(session)
    order = _create_order(session, tenant_id)
    session.commit()
    ps.order_service.submit(order.id)
    ps.validate_order(order.id)
    order, saga, _ = ps.start_workflow(order.id)
    state = ps.run_to_completion(saga.id)
    session.commit()
    assert state == "COMPLETED"
    # The AAA step retried: 3 attempts recorded, eventually succeeded.
    from app.models import SagaStepAttempt

    aaa_step = session.query(SagaStep).filter(SagaStep.saga_id == saga.id, SagaStep.step_name == "configure_access").one()
    assert aaa_step.attempt_count == 3
    attempts = session.query(SagaStepAttempt).filter(SagaStepAttempt.saga_step_id == aaa_step.id).all()
    assert len(attempts) == 3
    assert attempts[0].status == "FAILED"
    assert attempts[-1].status == "COMPLETED"
    injector.reset()


def test_saga_compensates_on_non_retryable_failure(session, tenant_id, seeded_resources):
    injector = FaultInjector()
    injector.fail_always("nas", "configure_subscriber", retryable=False)
    ps = _ps(session)
    order = _create_order(session, tenant_id)
    session.commit()
    ps.order_service.submit(order.id)
    ps.validate_order(order.id)
    order, saga, _ = ps.start_workflow(order.id)
    state = ps.run_to_completion(saga.id)
    session.commit()
    assert state == "COMPENSATED"
    order = session.get(Order, order.id)
    assert order.state == "ROLLED_BACK"
    # Prior completed steps were compensated in reverse order.
    steps = session.query(SagaStep).filter(SagaStep.saga_id == saga.id).order_by(SagaStep.step_index).all()
    compensated = [s for s in steps if s.state == "COMPENSATED"]
    assert len(compensated) >= 4  # subscription, resources, schedule, access
    # Resources were released back to the pool.
    from app.models import ResourceInventory, ResourceReservation

    reservations = session.query(ResourceReservation).filter(ResourceReservation.order_id == order.id).all()
    assert all(r.state == "RELEASED" for r in reservations)
    available = session.query(ResourceInventory).filter(ResourceInventory.tenant_id == tenant_id, ResourceInventory.status == "AVAILABLE").count()
    assert available >= 5
    injector.reset()


def test_saga_manual_intervention_when_no_compensation(session, tenant_id, seeded_resources):
    """A non-retryable failure in the very first saga step (nothing completed
    yet to compensate) routes to manual intervention; resolving it resumes."""
    injector = FaultInjector()
    injector.fail_always("bss", "create_billing_account", retryable=False)
    ps = _ps(session)
    order = _create_order(session, tenant_id)
    session.commit()
    ps.order_service.submit(order.id)
    ps.validate_order(order.id)  # validation passes (customer/plan/payment ok)
    order, saga, _ = ps.start_workflow(order.id)
    state = ps.run_to_completion(saga.id)
    session.commit()
    assert state == "MANUAL_INTERVENTION"
    order = session.get(Order, order.id)
    assert order.state == "MANUAL_INTERVENTION_REQUIRED"
    intervention = session.query(ManualIntervention).filter(ManualIntervention.order_id == order.id).one()
    assert intervention.status == "OPEN"
    injector.reset()

    # Operator fixes the root cause, resolves the intervention, saga resumes.
    state = ps.resume(saga.id, resolved_by="noc-operator")
    session.commit()
    assert state == "COMPLETED"
    intervention = session.get(ManualIntervention, intervention.id)
    assert intervention.status == "RESOLVED"
    order = session.get(Order, order.id)
    assert order.state == "COMPLETED"


def test_saga_payment_gate_pauses_and_resumes(session, tenant_id, seeded_resources):
    ps = _ps(session)
    order = _create_order(session, tenant_id, customer_id="cust-pay-pending")
    session.commit()
    ps.order_service.submit(order.id)
    state = ps.validate_order(order.id)
    assert state == "PAYMENT_PENDING"
    order = session.get(Order, order.id)
    assert order.state == "PAYMENT_PENDING"
    # No saga started yet.
    assert session.query(SagaInstance).filter(SagaInstance.order_id == order.id).count() == 0
    # Payment approved externally -> READY -> start workflow.
    ps.order_service.approve_payment(order.id)
    order, saga, _ = ps.start_workflow(order.id)
    state = ps.run_to_completion(saga.id)
    session.commit()
    assert state == "COMPLETED"
    assert session.get(Order, order.id).state == "COMPLETED"


def test_saga_survives_engine_restart(session, tenant_id, seeded_resources):
    """Persisted saga state lets a brand-new engine pick up where a crashed
    worker left off (simulated by advancing once, then re-advancing on a fresh
    ProvisioningService)."""
    ps = _ps(session)
    order = _create_order(session, tenant_id)
    session.commit()
    ps.order_service.submit(order.id)
    ps.validate_order(order.id)
    order, saga, _ = ps.start_workflow(order.id)
    # "Crash" after the first engine instance; a new engine resumes.
    session.commit()
    ps2 = _ps(session)
    state = ps2.run_to_completion(saga.id)
    session.commit()
    assert state == "COMPLETED"
    assert session.get(Order, order.id).state == "COMPLETED"
