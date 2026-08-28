"""M3 IP identity: search, regulatory lookup audit trail, and IP history."""
import uuid

from app.models import AccountingEvent, ActiveSession, AuditLog, IpLease, IpPool
from app.network_control.ip_identity import ip_history, regulatory_lookup, search_identity


def _pool(session, tenant):
    item = IpPool(tenant_id=tenant.id, name="pppoe", address_family="ipv4", cidr="198.51.100.0/24")
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def _session(session, tenant, nas, ip="198.51.100.10"):
    item = ActiveSession(
        tenant_id=tenant.id,
        nas_id=nas.id,
        subscriber_id=uuid.uuid4(),
        username="cust-a",
        session_id=f"ses-{uuid.uuid4().hex[:8]}",
        status="ACTIVE",
        started_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        framed_ip=ip,
        mac_address="aa:bb:cc:dd:ee:ff",
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def test_search_by_ip(session, tenant, nas):
    _session(session, tenant, nas, ip="198.51.100.10")
    rows = search_identity(session, tenant.id, ip="198.51.100.10")
    assert any(row["kind"] == "session" and row["framed_ip"] == "198.51.100.10" for row in rows)


def test_search_by_username_and_mac(session, tenant, nas):
    _session(session, tenant, nas)
    assert search_identity(session, tenant.id, username="cust-a")
    assert search_identity(session, tenant.id, mac="aa:bb:cc:dd:ee:ff")


def test_regulatory_lookup_is_audited(session, tenant, nas):
    _session(session, tenant, nas, ip="198.51.100.10")
    rows = regulatory_lookup(session, tenant.id, ip="198.51.100.10", actor="compliance-officer")
    session.commit()
    assert rows
    log = session.query(AuditLog).filter(AuditLog.tenant_id == tenant.id, AuditLog.action == "ip.regulatory_lookup").one()
    assert log.detail["actor"] == "compliance-officer"
    assert log.target_id == "198.51.100.10"


def test_ip_history(session, tenant, nas):
    pool = _pool(session, tenant)
    _session(session, tenant, nas, ip="198.51.100.10")
    lease = IpLease(tenant_id=tenant.id, pool_id=pool.id, subscriber_id=uuid.uuid4(), address="198.51.100.10", reservation=False)
    session.add(lease)
    session.commit()
    history = ip_history(session, tenant.id, "198.51.100.10")
    assert len(history["leases"]) == 1
    assert len(history["sessions"]) == 1
