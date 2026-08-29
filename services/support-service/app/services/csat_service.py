"""Customer satisfaction (CSAT) collection.

Collected after closure, once per ticket. Agents can never modify a submitted
rating; a low score raises a supervisor-review flag and alerts the team lead.
CSAT stays separate from operational SLA compliance reporting."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.exceptions import DuplicateError, ValidationError
from ..enums import CSAT_RATING_MAX, CSAT_RATING_MIN
from ..integrations.base import get_adapter
from ..models import CustomerSatisfaction, Ticket
from ..services.audit_service import append_event, correlation, outbox


def _now() -> datetime:
    return datetime.now(timezone.utc)


def submit_csat(session: Session, tenant_id, ticket: Ticket, *, rating: int, comment: str | None = None,
                channel: str = "CUSTOMER_PORTAL", correlation_id: str | None = None) -> dict:
    if not CSAT_RATING_MIN <= rating <= CSAT_RATING_MAX:
        raise ValidationError(f"rating must be between {CSAT_RATING_MIN} and {CSAT_RATING_MAX}")
    existing = session.scalars(select(CustomerSatisfaction).where(CustomerSatisfaction.ticket_id == ticket.id)).first()
    if existing is not None:
        raise DuplicateError("satisfaction already submitted for this ticket")
    csat = CustomerSatisfaction(
        tenant_id=tenant_id, ticket_id=ticket.id, rating=rating, comment=comment,
        channel=channel.upper(), agent_id=ticket.assigned_agent_id, team_id=ticket.assigned_team_id,
        category_id=ticket.category_id, low_score_reviewed=False,
    )
    session.add(csat)
    session.flush()
    ticket.csat_id = csat.id
    request_id = correlation(correlation_id)
    append_event(session, ticket, "ticket.csat_received",
                 payload={"rating": rating, "channel": channel.upper()},
                 actor_type="customer", actor_id=ticket.customer_id, correlation_id=request_id)
    outbox(session, "support.ticket.csat_received.v1", tenant_id, request_id,
           {"ticket_id": str(ticket.id), "ticket_number": ticket.ticket_number, "rating": rating})
    if rating <= 2:
        csat.low_score_reviewed = True
        try:
            get_adapter("notifications").send(
                channel="email", recipient=f"team-lead:{ticket.assigned_team_id}",
                template="csat_low_score",
                variables={"ticket_number": ticket.ticket_number, "rating": rating},
                ticket_id=ticket.id, correlation_id=request_id)
        except Exception:  # noqa: BLE001 — notification failure must not break CSAT
            pass
    session.flush()
    return {"csat_id": str(csat.id), "ticket_id": str(ticket.id), "rating": rating}
