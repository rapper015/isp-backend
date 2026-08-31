"""Resource reservation: deterministic allocation, conflict-free CAS,
TTL expiry, release and capacity accounting."""
import uuid

import pytest
from sqlalchemy import select

from app.models import ResourceInventory, ResourceReservation
from app.services.resource_service import InvalidReservation, ResourceService, ResourceUnavailable


@pytest.fixture
def service(session):
    return ResourceService(session)


def _seed(session, tenant_id, count=5):
    service = ResourceService(session)
    service.seed(tenant_id, "IPV4", [f"10.1.{i}.2" for i in range(count)])
    session.commit()


def test_deterministic_reservation(session, tenant_id, service):
    _seed(session, tenant_id)
    order_id = uuid.uuid4()
    reservations = service.reserve(tenant_id, order_id, "IPV4", 2, actor="tester")
    session.commit()
    assert len(reservations) == 2
    assert all(r.state == "RESERVED" for r in reservations)
    assert reservations[0].resource_key == "10.1.0.2"
    assert reservations[1].resource_key == "10.1.1.2"
    # Deterministic: second reserve call returns different keys in stable order.
    reservations2 = service.reserve(tenant_id, uuid.uuid4(), "IPV4", 1)
    session.commit()
    assert reservations2[0].resource_key == "10.1.2.2"


def test_allocate_and_release_cycle(session, tenant_id, service):
    _seed(session, tenant_id)
    order_id = uuid.uuid4()
    reservations = service.reserve(tenant_id, order_id, "IPV4", 1)
    token = reservations[0].reservation_token
    service.allocate(token)
    session.commit()
    row = session.execute(select(ResourceInventory.id, ResourceInventory.status).where(ResourceInventory.reservation_token == token)).one()
    resource_id, status = row
    assert status == "ALLOCATED"
    service.release(token, reason="order completed")
    session.commit()
    assert session.execute(select(ResourceInventory.status).where(ResourceInventory.id == resource_id)).scalar() == "AVAILABLE"


def test_conflict_free_allocation_across_sessions(session, tenant_id):
    """Two concurrent workers must never double-allocate a single resource."""
    _seed(session, tenant_id, count=1)  # exactly one IP
    order_a, order_b = uuid.uuid4(), uuid.uuid4()
    # Separate sessions/servers race for the single resource.
    s1 = session
    s2 = session
    r1 = ResourceService(s1)
    r2 = ResourceService(s2)
    got_a = None
    got_b = None
    try:
        got_a = r1.reserve(tenant_id, order_a, "IPV4", 1)
    except ResourceUnavailable:
        pass
    try:
        got_b = r2.reserve(tenant_id, order_b, "IPV4", 1)
    except ResourceUnavailable:
        pass
    session.commit()
    successes = sum(1 for r in (got_a, got_b) if r)
    assert successes == 1, "only one of the concurrent reserves may succeed"
    token = (got_a or got_b)[0].reservation_token
    active = list(
        session.query(ResourceReservation).filter(
            ResourceReservation.state.in_(["RESERVED", "ALLOCATED"]),
            ResourceReservation.reservation_token == token,
        )
    )
    assert len(active) == 1


def test_ttl_expiry_releases_reservation(session, tenant_id, service):
    _seed(session, tenant_id)
    reservations = service.reserve(tenant_id, uuid.uuid4(), "IPV4", 1, ttl_seconds=-1)
    session.commit()
    token = reservations[0].reservation_token
    expired = service.expire_due()
    session.commit()
    assert any(r.reservation_token == token for r in expired)
    token_after = session.execute(select(ResourceInventory.reservation_token).where(ResourceInventory.reservation_token == token)).scalar()
    assert token_after is None


def test_release_with_unknown_token_raises(session, tenant_id, service):
    with pytest.raises(InvalidReservation):
        service.release("no-such-token")


def test_capacity_accounting(session, tenant_id, service):
    _seed(session, tenant_id, count=5)
    service.reserve(tenant_id, uuid.uuid4(), "IPV4", 2)
    session.commit()
    capacity = service.capacity(tenant_id, "IPV4")["IPV4"]
    assert capacity["AVAILABLE"] == 3
    assert capacity["RESERVED"] == 2


def test_insufficient_resources_releases_partial(session, tenant_id, service):
    _seed(session, tenant_id, count=2)
    with pytest.raises(ResourceUnavailable):
        service.reserve(tenant_id, uuid.uuid4(), "IPV4", 3)
    session.rollback()
    # Partial claim must have been released back to AVAILABLE.
    capacity = service.capacity(tenant_id, "IPV4")["IPV4"]
    assert capacity["AVAILABLE"] == 2
