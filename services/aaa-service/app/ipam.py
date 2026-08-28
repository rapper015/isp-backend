"""AAA-owned lease allocation. It never configures FreeRADIUS SQL pools."""
import ipaddress
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session
from .models import IpLease, IpPool

class PoolExhausted(RuntimeError): pass
class InvalidPool(ValueError): pass

def validate_pool(cidr: str, family: str) -> str:
    network = ipaddress.ip_network(cidr, strict=True)
    if (family == "ipv4" and network.version != 4) or (family == "ipv6" and network.version != 6): raise InvalidPool("CIDR family does not match pool family")
    return str(network)

def allocate(session: Session, pool: IpPool, subscriber_id, session_id=None) -> IpLease:
    existing = session.scalar(select(IpLease).where(IpLease.tenant_id == pool.tenant_id, IpLease.pool_id == pool.id, IpLease.subscriber_id == subscriber_id, IpLease.released_at.is_(None)).limit(1))
    if existing:
        existing.active_session_id = session_id
        return existing
    network = ipaddress.ip_network(pool.cidr)
    occupied = set(session.scalars(select(IpLease.address).where(IpLease.tenant_id == pool.tenant_id, IpLease.released_at.is_(None))))
    excluded = set(pool.excluded)
    candidates = network.hosts() if network.version == 4 else iter(network)
    for address in candidates:
        value = str(address)
        if value not in occupied and value not in excluded:
            lease = IpLease(tenant_id=pool.tenant_id, pool_id=pool.id, subscriber_id=subscriber_id, address=value, active_session_id=session_id)
            session.add(lease); session.flush(); return lease
    raise PoolExhausted("pool exhausted")

def release(session: Session, lease: IpLease) -> None:
    lease.active_session_id = None
    if not lease.reservation: lease.released_at = datetime.now(timezone.utc)
