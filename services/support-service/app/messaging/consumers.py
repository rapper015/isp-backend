"""Idempotent inbound event consumers for the support service.

Every handler is gated by the consumer inbox so duplicate deliveries run at
most once. Tenant scope always comes from the event envelope (never from the
payload alone). Handlers only react — the support service never mutates another
service's data, it links references and updates its own tickets."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..events import canonical_event_type, consume_once
from ..models import Ticket
from ..services.audit_service import append_event
from ..services import outage_service, ticket_service
from ..services.escalation_service import evaluate_ticket, execute_escalation  # noqa: F401


def handle_event(session: Session, event: dict, consumer: str = "support-handler") -> dict:
    """Dispatch one inbound event; returns {'handled': bool, 'action': str}.

    Idempotent: duplicate event_ids for the same consumer are skipped."""
    event_type = canonical_event_type(event.get("event_type", ""))
    event_id = str(event.get("id") or event.get("event_id") or event.get("correlation_id") or "")
    tenant_id = event.get("tenant_id")
    payload = event.get("payload") or {}
    if not event_id:
        return {"handled": False, "action": "missing_event_id"}
    if not consume_once(session, event_id, consumer):
        return {"handled": False, "action": "duplicate"}
    if not tenant_id:
        return {"handled": False, "action": "missing_tenant"}

    try:
        action = _dispatch(session, event_type, tenant_id, payload)
    except Exception:  # noqa: BLE001 — a consumer failure must not kill the loop
        session.rollback()
        return {"handled": False, "action": "error"}
    session.commit()
    return {"handled": True, "action": action}


def _dispatch(session: Session, event_type: str, tenant_id, payload: dict) -> str:
    from uuid import UUID

    try:
        tenant_uuid = UUID(str(tenant_id))
    except ValueError:
        return "invalid_tenant"

    if event_type == "nms.outage_detected.v1":
        linked = outage_service.auto_associate_tickets(session, tenant_uuid, payload, actor="nms-consumer")
        return f"linked:{len(linked)}"
    if event_type == "nms.outage_cleared.v1":
        incident_id = payload.get("incident_id") or payload.get("outage_id") or ""
        result = outage_service.handle_outage_cleared(session, tenant_uuid, incident_id, actor="nms-consumer")
        return f"verification:{len(result['verification_needed'])}"
    if event_type == "oss.order.completed.v1":
        return _advance_order_tickets(session, tenant_uuid, payload, ok=True)
    if event_type == "oss.order.failed.v1":
        return _advance_order_tickets(session, tenant_uuid, payload, ok=False)
    if event_type == "workforce.job_completed.v1":
        return _advance_job_tickets(session, tenant_uuid, payload)
    if event_type == "bss.payment.captured.v1":
        return _note_payment(session, tenant_uuid, payload)
    if event_type in ("aaa.session.started.v1", "aaa.session.stopped.v1"):
        return _note_session(session, tenant_uuid, payload)
    if event_type == "crm.customer.updated.v1":
        return _refresh_customer_projection(session, tenant_uuid, payload)
    return "ignored"


def _tickets_by_order(session: Session, tenant_id, order_id: str | None, order_number: str | None) -> list[Ticket]:
    stmt = select(Ticket).where(Ticket.tenant_id == tenant_id)
    if order_id:
        stmt = stmt.where(Ticket.oss_order_id == order_id)
    elif order_number:
        stmt = stmt.where(Ticket.oss_order_number == order_number)
    else:
        return []
    return list(session.scalars(stmt))


def _advance_order_tickets(session: Session, tenant_id, payload: dict, *, ok: bool) -> str:
    order_id = payload.get("order_id")
    order_number = payload.get("order_number")
    count = 0
    for ticket in _tickets_by_order(session, tenant_id, order_id, order_number):
        if ok and ticket.status == "PENDING_OSS_ORDER":
            try:
                ticket_service.accept(session, tenant_id, ticket.id, actor="oss-consumer",
                                      correlation_id=ticket.correlation_id)
            except Exception:  # noqa: BLE001
                pass
            count += 1
        elif not ok and ticket.status in ("PENDING_OSS_ORDER", "IN_PROGRESS", "ASSIGNED"):
            append_event(session, ticket, "ticket.oss_order_failed",
                         payload={"order_id": order_id, "order_number": order_number},
                         actor_type="system", actor_id="oss-consumer", correlation_id=ticket.correlation_id)
            try:
                execute_escalation(session, ticket, trigger="FAILED_OSS_ORDER",
                                   reason="linked OSS order failed", actor="oss-consumer",
                                   correlation_id=ticket.correlation_id)
            except Exception:  # noqa: BLE001
                pass
            count += 1
    return f"orders:{count}"


def _advance_job_tickets(session: Session, tenant_id, payload: dict) -> str:
    job_id = payload.get("job_id")
    job_number = payload.get("job_number")
    count = 0
    stmt = select(Ticket).where(Ticket.tenant_id == tenant_id)
    if job_id:
        stmt = stmt.where(Ticket.workforce_job_id == job_id)
    elif job_number:
        stmt = stmt.where(Ticket.workforce_job_number == job_number)
    else:
        return "jobs:0"
    for ticket in list(session.scalars(stmt)):
        if ticket.status == "PENDING_FIELD_VISIT":
            try:
                ticket_service.accept(session, tenant_id, ticket.id, actor="workforce-consumer",
                                      correlation_id=ticket.correlation_id)
            except Exception:  # noqa: BLE001
                pass
            count += 1
    return f"jobs:{count}"


def _note_payment(session: Session, tenant_id, payload: dict) -> str:
    customer_id = payload.get("customer_id")
    if not customer_id:
        return "payment:no_customer"
    tickets = list(session.scalars(
        select(Ticket).where(Ticket.tenant_id == tenant_id, Ticket.customer_id == customer_id,
                             Ticket.status.in_(("PENDING_BILLING_ACTION", "IN_PROGRESS", "ASSIGNED")))))
    for ticket in tickets:
        append_event(session, ticket, "ticket.payment_captured",
                     payload={"reference": payload.get("payment_reference")},
                     actor_type="system", actor_id="bss-consumer", correlation_id=ticket.correlation_id)
    return f"payment:{len(tickets)}"


def _note_session(session: Session, tenant_id, payload: dict) -> str:
    username = payload.get("username") or payload.get("subscriber_username")
    if not username:
        return "session:no_username"
    tickets = list(session.scalars(
        select(Ticket).where(Ticket.tenant_id == tenant_id, Ticket.subscriber_username == username,
                             Ticket.status.notin_(("CLOSED", "CANCELLED", "DUPLICATE")))))
    for ticket in tickets:
        append_event(session, ticket, "ticket.session_event",
                     payload={"event": payload.get("event_type"), "session_id": payload.get("session_id")},
                     actor_type="system", actor_id="aaa-consumer", correlation_id=ticket.correlation_id)
    return f"session:{len(tickets)}"


def _refresh_customer_projection(session: Session, tenant_id, payload: dict) -> str:
    customer_id = payload.get("customer_id")
    if not customer_id:
        return "customer:no_id"
    tickets = list(session.scalars(
        select(Ticket).where(Ticket.tenant_id == tenant_id, Ticket.customer_id == customer_id)))
    for ticket in tickets:
        if payload.get("name"):
            ticket.customer_name = payload["name"]
        if payload.get("tier"):
            ticket.customer_tier = payload["tier"]
    return f"customer:{len(tickets)}"
