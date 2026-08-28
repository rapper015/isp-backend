from uuid import uuid4
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database import Base
from app.ipam import allocate, validate_pool
from app.models import IpPool, Tenant

def test_tenant_pool_allocation_is_stable_and_unique():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    tenant = Tenant(name="test"); session.add(tenant); session.flush()
    pool = IpPool(tenant_id=tenant.id, name="main", cidr=validate_pool("10.1.0.0/30", "ipv4")); session.add(pool); session.flush()
    subscriber = uuid4()
    first = allocate(session, pool, subscriber)
    assert first.address == "10.1.0.1"
    assert allocate(session, pool, subscriber).id == first.id
