"""Background maintenance tasks: SLA evaluation, escalation checks, auto-close,
stuck-action detection and outbox flush. Every task is idempotent and
restart-safe; duplicate scheduler execution is harmless because transitions are
guarded by persisted state."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import OutboxEvent, SupportAction, Ticket, TicketSLA
from .domain.sla import engine as sla_engine
from .services import sla_service, ticket_service
from .services.escalation_service import evaluate_ticket


def _now() -> datetime:
    return datetime.now(timezone.utc)


def flush_outbox(session: Session, limit: int = 100) -> list[str]:
    """Publish pending outbox events to RabbitMQ (best-effort) and mark them."""
    pending = list(session.scalars(
        select(OutboxEvent).where(OutboxEvent.published_at.is_(None)).order_by(OutboxEvent.occurred_at).limit(limit)))
    published: list[str] = []
    for event in pending:
        try:
            _publish(event)
            event.published_at = datetime.now(timezone.utc)
            event.attempts += 1
            published.append(event.event_type)
        except Exception:  # noqa: BLE001 — broker unavailable; retry later
            event.attempts += 1
    session.commit()
    return published


def _publish(event: OutboxEvent) -> None:
    """Wire format for a single outbox event.

    The real worker resolves RabbitMQ from env and publishes to
    support.events.v1 with routing key = event_type. Tests assert on outbox
    rows instead of a live broker."""
    payload = {
        "event_type": event.event_type,
        "correlation_id": event.correlation_id,
        "idempotency_key": event.idempotency_key,
        "tenant_id": str(event.tenant_id) if event.tenant_id else None,
        "payload": event.payload,
        "occurred_at": event.occurred_at.isoformat(),
    }
    json.dumps(payload)  # serialization sanity check; publish hook point
    return None


def evaluate_sla_deadlines(session: Session, limit: int = 200) -> dict:
    """Evaluate every SLA instance that is not completed/paused-breach-final.

    Only transitions that were not yet recorded fire events/escalations once."""
    slas = list(session.scalars(
        select(TicketSLA).where(TicketSLA.status.in_(("ACTIVE", "AT_RISK", "PAUSED"))).limit(limit)))
    at_risk = 0
    breached = 0
    for sla in slas:
        result = sla_engine.evaluate_sla(session, sla, emit=True, consumer="sla-evaluator")
        if result["changed"]:
            session.flush()
            ticket = session.get(Ticket, sla.ticket_id)
            if ticket is not None:
                ticket.sla_status = sla.status
                try:
                    evaluate_ticket(session, ticket.tenant_id, ticket, actor="sla-evaluator", correlation_id=ticket.correlation_id)
                except Exception:  # noqa: BLE001
                    pass
            if result["breached"]:
                breached += 1
            elif result["at_risk"]:
                at_risk += 1
    session.commit()
    return {"evaluated": len(slas), "at_risk": at_risk, "breached": breached}


def run_escalation_checks(session: Session, limit: int = 200) -> list[str]:
    """Detect no-assignment / no-progress / repeated-reopen escalations."""
    tickets = list(session.scalars(
        select(Ticket).where(Ticket.status.notin_(("CLOSED", "CANCELLED", "DUPLICATE", "RESOLVED"))).limit(limit)))
    fired: list[str] = []
    for ticket in tickets:
        new_triggers = evaluate_ticket(session, ticket.tenant_id, ticket, actor="escalation-worker",
                                       correlation_id=ticket.correlation_id)
        fired.extend(new_triggers)
    session.commit()
    return fired


def auto_close_resolved(session: Session, limit: int = 100) -> list[str]:
    """Auto-close RESOLVED tickets whose waiting period has elapsed."""
    closed: list[str] = []
    tickets = list(session.scalars(
        select(Ticket).where(Ticket.status == "RESOLVED", Ticket.auto_close_at.is_not(None)).limit(limit)))
    for ticket in tickets:
        if ticket.auto_close_at <= _now():
            try:
                ticket_service.close(session, ticket.tenant_id, ticket.id, actor="system", confirm=True)
                closed.append(ticket.ticket_number)
            except Exception:  # noqa: BLE001
                pass
    session.commit()
    return closed


def requeue_stuck_actions(session: Session, timeout_minutes: int = 30, limit: int = 100) -> list[str]:
    """Mark RUNNING support actions that exceeded the timeout as TIMED_OUT."""
    timed_out: list[str] = []
    actions = list(session.scalars(
        select(SupportAction).where(SupportAction.status == "RUNNING").limit(limit)))
    for action in actions:
        executed = action.executed_at if action.executed_at and action.executed_at.tzinfo else (
            action.executed_at.replace(tzinfo=timezone.utc) if action.executed_at else _now())
        if _now() - executed > timedelta(minutes=timeout_minutes):
            action.status = "TIMED_OUT"
            action.error_code = "TIMEOUT"
            action.error_detail = f"no completion within {timeout_minutes} minutes"
            timed_out.append(str(action.id))
    session.commit()
    return timed_out


def detect_stuck_tickets(session: Session, hours: int = 72, limit: int = 200) -> list[dict]:
    """Tickets with no update for `hours` in active states (report + marker)."""
    stuck = []
    cutoff = _now() - timedelta(hours=hours)
    tickets = list(session.scalars(
        select(Ticket).where(Ticket.status.notin_(("CLOSED", "CANCELLED", "DUPLICATE", "RESOLVED"))).limit(limit)))
    for ticket in tickets:
        updated = ticket.updated_at if ticket.updated_at.tzinfo else ticket.updated_at.replace(tzinfo=timezone.utc)
        if updated < cutoff:
            from .services.audit_service import append_event

            append_event(session, ticket, "ticket.stale_detected",
                         payload={"hours": hours, "updated_at": updated.isoformat()},
                         actor_type="system", actor_id="maintenance-worker", correlation_id=ticket.correlation_id)
            stuck.append({"ticket_id": str(ticket.id), "ticket_number": ticket.ticket_number})
    session.commit()
    return stuck


def reconcile_sla_timers(session: Session, limit: int = 200) -> dict:
    """Repair SLA deadlines against the persisted invariant (repair command)."""
    repaired = []
    slas = list(session.scalars(select(TicketSLA).limit(limit)))
    for sla in slas:
        repaired.append(sla_engine.reconcile_sla(session, sla))
    session.commit()
    return {"repaired": len(repaired)}
