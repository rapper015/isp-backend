"""IP identity and regulatory lookup.

IPAM (aaa_ip_pools / aaa_ip_leases) remains the authoritative owner of IP
addresses. This module provides searchable identity linkage (customer, session,
MAC, NAS) and a strictly-audited regulatory lookup protected by RBAC."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AccountingEvent, ActiveSession, IpLease, Nas
from ..services import audit, correlation


def search_identity(
    session: Session,
    tenant_id,
    *,
    ip: str | None = None,
    username: str | None = None,
    mac: str | None = None,
    session_id: str | None = None,
    nas_id=None,
    customer_ref: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Search current identity linkage across sessions, leases and accounting."""
    rows: list[dict] = []
    if ip or username or mac or session_id or nas_id:
        stmt = select(ActiveSession).where(ActiveSession.tenant_id == tenant_id)
        if ip:
            stmt = stmt.where(ActiveSession.framed_ip == ip)
        if username:
            stmt = stmt.where(ActiveSession.username == username)
        if mac:
            stmt = stmt.where(ActiveSession.mac_address == mac)
        if session_id:
            stmt = stmt.where(ActiveSession.session_id == session_id)
        if nas_id:
            stmt = stmt.where(ActiveSession.nas_id == nas_id)
        for item in session.scalars(stmt.limit(limit)):
            rows.append(_session_row(item, "session"))
    if ip:
        for lease in session.scalars(select(IpLease).where(IpLease.tenant_id == tenant_id, IpLease.address == ip).limit(limit)):
            rows.append({"kind": "lease", "id": str(lease.id), "address": lease.address, "subscriber_id": str(lease.subscriber_id) if lease.subscriber_id else None, "reservation": lease.reservation, "released_at": lease.released_at})
    return rows


def regulatory_lookup(session: Session, tenant_id, *, ip: str, actor: str, limit: int = 50) -> list[dict]:
    """Authorized historical lookup. Always audited; caller must hold the
    aaa.ip.regulatory_lookup permission (enforced by the API layer)."""
    rows: list[dict] = []
    for item in session.scalars(select(ActiveSession).where(ActiveSession.tenant_id == tenant_id, ActiveSession.framed_ip == ip).order_by(ActiveSession.started_at.desc()).limit(limit)):
        rows.append(_session_row(item, "session"))
    for lease in session.scalars(select(IpLease).where(IpLease.tenant_id == tenant_id, IpLease.address == ip).order_by(IpLease.created_at.desc()).limit(limit)):
        rows.append({"kind": "lease", "id": str(lease.id), "address": lease.address, "subscriber_id": str(lease.subscriber_id) if lease.subscriber_id else None, "reservation": lease.reservation, "released_at": lease.released_at, "created_at": lease.created_at})
    for event in session.scalars(select(AccountingEvent).where(AccountingEvent.tenant_id == tenant_id).order_by(AccountingEvent.received_at.desc()).limit(1000)):
        if ip in event.raw_redacted.get("Framed-IP-Address", "") or ip in event.raw_redacted.get("framed_ip", ""):
            rows.append({"kind": "accounting", "id": str(event.id), "session_id": event.session_id, "event_type": event.event_type, "received_at": event.received_at})
            if len(rows) >= limit:
                break
    request_id = correlation(None)
    audit(session, tenant_id, "ip.regulatory_lookup", ip, request_id, {"actor": actor, "result_count": len(rows)})
    return rows


def ip_history(session: Session, tenant_id, address: str, limit: int = 50) -> dict:
    leases = [{"id": str(item.id), "subscriber_id": str(item.subscriber_id) if item.subscriber_id else None, "reservation": item.reservation, "released_at": item.released_at, "created_at": item.created_at} for item in session.scalars(select(IpLease).where(IpLease.tenant_id == tenant_id, IpLease.address == address).order_by(IpLease.created_at.desc()).limit(limit))]
    sessions = [{"session_id": item.session_id, "username": item.username, "status": item.status, "started_at": item.started_at, "last_interim_at": item.last_interim_at, "framed_ip": item.framed_ip} for item in session.scalars(select(ActiveSession).where(ActiveSession.tenant_id == tenant_id, ActiveSession.framed_ip == address).order_by(ActiveSession.started_at.desc()).limit(limit))]
    return {"address": address, "leases": leases, "sessions": sessions}


def _session_row(item: ActiveSession, kind: str) -> dict:
    return {
        "kind": kind,
        "id": str(item.id),
        "session_id": item.session_id,
        "username": item.username,
        "status": item.status,
        "nas_id": str(item.nas_id),
        "subscriber_id": str(item.subscriber_id) if item.subscriber_id else None,
        "framed_ip": item.framed_ip,
        "mac_address": item.mac_address,
        "started_at": item.started_at,
        "last_interim_at": item.last_interim_at,
        "policy_snapshot": item.policy_snapshot,
    }
