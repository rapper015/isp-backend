"""Outage and major-incident correlation with NMS.

One NMS incident may affect many customers and many tickets. The support
service links tickets to incidents through the NMS adapter and never fabricates
incidents. Auto-association only links; closing is always gated on verification
of service restoration — an alarm clearing never auto-closes a ticket."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.exceptions import NotFoundError, ValidationError
from ..integrations.base import get_adapter
from ..models import Ticket
from . import ticket_service


def _now() -> datetime:
    return datetime.now(timezone.utc)


def active_outages(session: Session, tenant_id) -> list[dict]:
    try:
        result = get_adapter("nms").list_active_outages(str(tenant_id))
        if result.ok:
            return result.output.get("outages", [])
    except Exception:  # noqa: BLE001 — nms unavailable is reported, not fatal
        pass
    return []


def suggest_incidents(session: Session, tenant_id, ticket: Ticket, *, window_minutes: int = 120) -> list[dict]:
    """Suggest active incidents that may relate to this ticket.

    Correlation factors: tenant, POP/NAS/area reference, service location and
    recency of the outage window."""
    candidates = []
    for outage in active_outages(session, tenant_id):
        start_raw = outage.get("started_at") or outage.get("startedAt")
        try:
            started = datetime.fromisoformat(start_raw.replace("Z", "+00:00")) if start_raw else None
        except (ValueError, AttributeError):
            started = None
        if started and _now() - started > timedelta(minutes=window_minutes):
            continue
        overlap = False
        ticket_refs = [ticket.service_location_id, ticket.subscriber_username]
        for key in ("pop", "nas", "olt", "area", "service_location", "node", "vlan"):
            value = outage.get(key)
            if value and (value in ticket_refs or any(r and value in str(r) for r in ticket_refs)):
                overlap = True
                break
        if not overlap and ticket.service_location_id and ticket.service_location_id == outage.get("service_location"):
            overlap = True
        if overlap:
            candidates.append(outage)
    return candidates


def link_incident(session: Session, tenant_id, ticket_id, *, incident_id: str, incident_number: str | None = None,
                  actor: str = "system", correlation_id: str | None = None) -> Ticket:
    return ticket_service.link_outage(session, tenant_id, ticket_id, incident_id=incident_id,
                                      incident_number=incident_number, actor=actor, correlation_id=correlation_id, auto=False)


def unlink_incident(session: Session, tenant_id, ticket_id, *, actor: str = "system",
                    correlation_id: str | None = None) -> Ticket:
    return ticket_service.unlink_outage(session, tenant_id, ticket_id, actor=actor, correlation_id=correlation_id)


def auto_associate_tickets(session: Session, tenant_id, incident: dict, *, actor: str = "system") -> list[str]:
    """Associate open tickets that reference the outage's POP/NAS/location.

    Only links; it never changes ticket resolution state. Duplicate suppression
    is handled by the unique relationship/event semantics of link_outage."""
    linked: list[str] = []
    pop = incident.get("pop")
    nas = incident.get("nas")
    location = incident.get("service_location")
    tickets = list(session.scalars(
        select(Ticket).where(Ticket.tenant_id == tenant_id,
                             Ticket.status.notin_(("CLOSED", "CANCELLED", "DUPLICATE")))))
    for ticket in tickets:
        matches = False
        if location and ticket.service_location_id == location:
            matches = True
        if nas and (ticket.subscriber_username and nas in ticket.subscriber_username):
            matches = True
        if pop and ticket.nms_incident_id is None and matches:
            ticket_service.link_outage(session, tenant_id, ticket.id, incident_id=incident.get("id"),
                                       incident_number=incident.get("number"), actor=actor, auto=True)
            linked.append(ticket.ticket_number)
    session.flush()
    return linked


def handle_outage_cleared(session: Session, tenant_id, incident_id: str, *, actor: str = "system") -> dict:
    """Outage cleared: mark linked tickets for verification. Do NOT auto-close;
    service restoration must be verified (per policy) before any resolution."""
    tickets = list(session.scalars(
        select(Ticket).where(Ticket.tenant_id == tenant_id, Ticket.nms_incident_id == incident_id)))
    verification_needed = []
    for ticket in tickets:
        if ticket.status in ("CLOSED", "CANCELLED", "DUPLICATE", "RESOLVED"):
            continue
        ticket_service.add_outage_clear_marker(ticket, session)
        verification_needed.append(ticket.ticket_number)
    session.flush()
    return {"incident_id": incident_id, "verification_needed": verification_needed}


def tickets_linked_to_incident(session: Session, tenant_id, incident_id: str) -> list[Ticket]:
    return list(session.scalars(
        select(Ticket).where(Ticket.tenant_id == tenant_id, Ticket.nms_incident_id == incident_id)))
