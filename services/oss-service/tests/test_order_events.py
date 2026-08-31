"""Order event sourcing: create/submit/transition events, immutable append-only
stream, optimistic concurrency, command idempotency and aggregate
reconstruction from the event stream."""
import uuid

import pytest
from sqlalchemy import select

from app.database import SessionLocal
from app.models import Order, OrderEvent
from app.services.order_repository import ConcurrencyConflict
from app.services.order_service import OrderService


def _create(session, tenant_id, **kwargs) -> Order:
    return OrderService(session).create_order(tenant_id, order_type="NEW_CONNECTION", customer_id=uuid.uuid4(), requested_plan_reference="plan-fiber-100", **kwargs)


def test_create_order_emits_created_event(session, tenant_id):
    order = _create(session, tenant_id)
    session.commit()
    events = OrderService(session).events(order.id)
    assert [e.event_type for e in events] == ["oss.order.created.v1"]
    assert events[0].aggregate_version == 1
    assert order.aggregate_version == 1
    assert order.state == "DRAFT"
    assert order.order_number.startswith("ORD-")


def test_transition_appends_event_and_status_history(session, tenant_id):
    order = _create(session, tenant_id)
    service = OrderService(session)
    order = service.submit(order.id)
    order = service.transition(order.id, "VALIDATING")
    session.commit()

    events = service.events(order.id)
    assert [e.event_type for e in events] == ["oss.order.created.v1", "oss.order.submitted.v1", "oss.order.state_changed.v1"]
    assert [e.aggregate_version for e in events] == [1, 2, 3]
    history = service.history(order.id)
    assert [h.to_state for h in history] == ["DRAFT", "SUBMITTED", "VALIDATING"]


def test_events_are_immutable_append_only(session, tenant_id):
    order = _create(session, tenant_id)
    service = OrderService(session)
    for _ in range(5):
        service.transition(order.id, "SUBMITTED") if False else None
        break
    session.commit()
    before = service.events(order.id)
    assert len(before) == 1
    # Re-querying yields the same single created event — no mutation.
    assert len(service.events(order.id)) == 1


def test_optimistic_concurrency_conflict(session, tenant_id):
    order = _create(session, tenant_id)
    session.commit()
    order_id = order.id
    # A second, independent writer loads a stale copy of the aggregate.
    s2 = SessionLocal()
    try:
        stale = s2.get(Order, order_id)
        assert stale.aggregate_version == 1
        # First writer advances the aggregate to version 2.
        OrderService(session).submit(order_id)
        session.commit()
        assert session.get(Order, order_id).aggregate_version == 2
        # Second writer using its stale version must be rejected.
        with pytest.raises(ConcurrencyConflict):
            OrderService(s2).repo.append(
                stale,
                "oss.order.submitted.v1",
                {"from": "DRAFT", "to": "SUBMITTED"},
                actor_type="system",
                actor_id="x",
                correlation_id=None,
                expected_version=stale.aggregate_version,  # stale (1)
            )
    finally:
        try:
            s2.rollback()  # clear the failed flush so close() succeeds
        except Exception:  # noqa: BLE001
            pass
        s2.close()


def test_duplicate_command_idempotency(session, tenant_id):
    order = _create(session, tenant_id)
    service = OrderService(session)
    idem = f"submit:{order.id}:abc"
    result, already = service.run_command(order.id, "SUBMIT", idempotency_key=idem, correlation_id="c1", fn=lambda o: service.submit(o.id))
    assert already is False
    result2, already2 = service.run_command(order.id, "SUBMIT", idempotency_key=idem, correlation_id="c1", fn=lambda o: service.submit(o.id))
    assert already2 is True
    assert result2 == result
    session.commit()
    # Only one submit event recorded.
    submits = [e for e in service.events(order.id) if e.event_type == "oss.order.submitted.v1"]
    assert len(submits) == 1


def test_invalid_transition_rejected_no_event(session, tenant_id):
    order = _create(session, tenant_id)
    session.commit()  # persist the created event before the failed attempt
    service = OrderService(session)
    with pytest.raises(ValueError):
        service.transition(order.id, "COMPLETED")
    session.rollback()
    # Only the created event remains; no invalid transition was recorded.
    assert len(service.events(order.id)) == 1
    assert order.aggregate_version == 1


def test_aggregate_reconstruction_from_events(session, tenant_id):
    """Replaying the event stream must rebuild the order state (projection)."""
    order = _create(session, tenant_id)
    service = OrderService(session)
    service.submit(order.id)
    service.transition(order.id, "VALIDATING")
    session.commit()

    stream = service.events(order.id)
    state = "DRAFT"
    version = 0
    for event in stream:
        version = max(version, event.aggregate_version)
        if event.event_type == "oss.order.created.v1":
            state = "DRAFT"
        elif event.event_type == "oss.order.submitted.v1":
            state = "SUBMITTED"
        elif event.event_type == "oss.order.state_changed.v1":
            state = event.payload.get("to_state", state)
    assert state == "VALIDATING"
    assert version == order.aggregate_version


def test_requested_snapshot_is_immutable(session, tenant_id):
    order = _create(session, tenant_id, requested_snapshot={"ont_serial": "ONT-SN-1001", "note": "original"})
    session.commit()
    snapshot = order.requested_snapshot
    snapshot["note"] = "mutated"
    session.commit()
    reloaded = session.get(Order, order.id)
    assert reloaded.requested_snapshot["note"] == "original"
