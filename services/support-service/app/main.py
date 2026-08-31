"""Support Service — governed customer support, ticketing, SLA, escalation,
diagnostics, controlled actions, outage correlation, knowledge and CSAT
(Milestone 5).

Explicit domain-command endpoints only — there is no `PATCH /tickets/{id}
{"status": ...}`. Customer-visible data is filtered by the portal serializers.
"""
from contextlib import asynccontextmanager
import secrets
from os import getenv
from typing import Any
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, Request, UploadFile, File, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import models  # noqa: F401  (register all tables)
from .database import Base, SessionLocal, engine
from .domain import sla as sla_domain
from .domain.exceptions import SupportError
from .integrations.fakes import STATE  # noqa: F401  (dev/test knobs)
from .models import (
    CustomerSatisfaction,
    KnowledgeArticle,
    SLAPolicy,
    SupportAction,
    Ticket,
    TicketComment,
    TicketDiagnosticSnapshot,
    TicketEvent,
    TicketSLA,
)
from .schemas import (
    ActionRequestIn,
    AgentIn,
    ApproveIn,
    ArticleCreate,
    ArticleUpdate,
    AssignIn,
    CancelActionIn,
    CancelIn,
    CategoryIn,
    CommentIn,
    CSATIn,
    DuplicateIn,
    EscalateIn,
    InboundMessageIn,
    InternalNoteIn,
    LinkDisputeIn,
    LinkIncidentIn,
    LinkJobIn,
    LinkOrderIn,
    RelatedIn,
    ReopenIn,
    ResolveIn,
    RoutingRuleIn,
    SLAOverrideIn,
    SLAPolicyCreate,
    SLAPolicyVersionCreate,
    ActivateVersionIn,
    TicketCreate,
    TransferIn,
    WatcherIn,
    PriorityIn,
)
from .security import customer_auth, internal_service_auth, management_auth, portal_principal
from .services import (
    action_service,
    assignment_service,
    catalog_service,
    communication_service,
    csat_service,
    diagnostic_service,
    escalation_service,
    knowledge_service,
    outage_service,
    sla_service,
    ticket_service,
)
from .services.audit_service import correlation, ticket_events


@asynccontextmanager
async def lifespan(_app: FastAPI):
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        catalog_service.ensure_global_defaults(session)
        session.commit()
    finally:
        session.close()
    yield


app = FastAPI(title="Support Service", version="5.0.0", lifespan=lifespan)


def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _raise(error: Exception) -> None:
    if isinstance(error, SupportError):
        raise HTTPException(error.status_code, {"code": error.code, "detail": error.message}) from error
    raise HTTPException(422, str(error)) from error


def _actor(request: Request) -> str:
    principal = getattr(request.state, "support_principal", None)
    if principal:
        return principal["subject"]
    return "system"


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------
def serialize_ticket(t: Ticket, *, include_internal: bool = True) -> dict:
    data = {
        "id": str(t.id),
        "ticket_number": t.ticket_number,
        "ticket_type": t.ticket_type,
        "subject": t.subject,
        "description": t.description,
        "customer_id": t.customer_id,
        "customer_number": t.customer_number,
        "customer_name": t.customer_name,
        "customer_tier": t.customer_tier,
        "service_subscription_id": t.service_subscription_id,
        "subscriber_username": t.subscriber_username,
        "service_location_id": t.service_location_id,
        "billing_account_id": t.billing_account_id,
        "franchise_id": t.franchise_id,
        "reseller_id": t.reseller_id,
        "source_channel": t.source_channel,
        "status": t.status,
        "customer_status": t.customer_visible_status(),
        "priority": t.priority,
        "impact": t.impact,
        "urgency": t.urgency,
        "severity": t.severity,
        "escalation_level": t.escalation_level,
        "assigned_queue_id": str(t.assigned_queue_id) if t.assigned_queue_id else None,
        "assigned_team_id": str(t.assigned_team_id) if t.assigned_team_id else None,
        "assigned_agent_id": t.assigned_agent_id,
        "assigned_agent_name": t.assigned_agent_name,
        "sla_status": t.sla_status,
        "response_deadline": t.response_deadline.isoformat() if t.response_deadline else None,
        "resolution_deadline": t.resolution_deadline.isoformat() if t.resolution_deadline else None,
        "resolved_at": t.resolved_at.isoformat() if t.resolved_at else None,
        "closed_at": t.closed_at.isoformat() if t.closed_at else None,
        "reopened_count": t.reopened_count,
        "oss_order_id": t.oss_order_id,
        "oss_order_number": t.oss_order_number,
        "nms_incident_id": t.nms_incident_id,
        "nms_incident_number": t.nms_incident_number,
        "workforce_job_id": t.workforce_job_id,
        "workforce_job_number": t.workforce_job_number,
        "billing_dispute_id": t.billing_dispute_id,
        "resolution_code": t.resolution_code,
        "resolution_summary": t.resolution_summary if include_internal else None,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }
    return data


def serialize_comment(c: TicketComment, *, include_internal: bool) -> dict:
    return {
        "id": str(c.id),
        "ticket_id": str(c.ticket_id),
        "direction": c.direction,
        "channel": c.channel,
        "kind": c.kind,
        "visibility": c.visibility,
        "body": c.sanitized_body if (c.visibility == "PUBLIC" or include_internal) else None,
        "sender_type": c.sender_type,
        "sender_id": c.sender_id if include_internal else None,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


def serialize_action(a: SupportAction) -> dict:
    return {
        "id": str(a.id),
        "ticket_id": str(a.ticket_id),
        "action_type": a.action_type,
        "status": a.status,
        "disruptive": a.disruptive,
        "requires_authorization": a.requires_authorization,
        "payload": a.payload,
        "result": a.result,
        "requested_by": a.requested_by,
        "approved_by": a.approved_by,
        "requested_at": a.requested_at.isoformat() if a.requested_at else None,
        "approved_at": a.approved_at.isoformat() if a.approved_at else None,
        "executed_at": a.executed_at.isoformat() if a.executed_at else None,
        "error_code": a.error_code,
        "error_detail": a.error_detail,
    }


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok", "service": getenv("SERVICE_NAME", "support-service")}


@app.get("/status")
def service_status():
    return {"service": "support", "phase": "milestone-5-support-ticketing"}


@app.get("/api/support/status", dependencies=[Depends(management_auth)])
def api_status():
    return {"service": "support", "capabilities": ["tickets", "sla", "escalation", "diagnostics", "actions", "outage", "csat", "knowledge"]}


# ===========================================================================
# Tickets — management
# ===========================================================================
@app.post("/api/support/tickets", status_code=status.HTTP_201_CREATED, dependencies=[Depends(management_auth)])
def create_ticket(payload: TicketCreate, request: Request, session: Session = Depends(db)):
    try:
        ticket = ticket_service.create_ticket(
            session, _tenant_of(payload), **ticket_kwargs(payload),
            actor=_actor(request), actor_type="agent",
        )
        session.commit()
        session.refresh(ticket)
        return serialize_ticket(ticket)
    except SupportError as error:
        _raise(error)


def _tenant_of(payload) -> UUID:
    from os import getenv

    tenant = getattr(payload, "tenant_id", None)
    if tenant:
        return tenant
    raise HTTPException(422, "tenant_id is required")


def ticket_kwargs(payload: TicketCreate) -> dict:
    return {
        "ticket_type": payload.ticket_type,
        "subject": payload.subject,
        "description": payload.description,
        "customer_id": payload.customer_id,
        "customer_number": payload.customer_number,
        "customer_name": payload.customer_name,
        "customer_tier": payload.customer_tier,
        "service_subscription_id": payload.service_subscription_id,
        "subscriber_username": payload.subscriber_username,
        "service_location_id": payload.service_location_id,
        "billing_account_id": payload.billing_account_id,
        "franchise_id": payload.franchise_id,
        "reseller_id": payload.reseller_id,
        "branch_id": payload.branch_id,
        "category_code": payload.category_code,
        "subcategory_code": payload.subcategory_code,
        "source_channel": payload.source_channel,
        "impact": payload.impact,
        "urgency": payload.urgency,
        "priority": payload.priority,
        "correlation_id": payload.correlation_id,
        "idempotency_key": payload.idempotency_key,
        "extra_tags": payload.tags,
    }


@app.get("/api/support/tickets", dependencies=[Depends(management_auth)])
def list_tickets(
    tenant_id: UUID | None = Query(default=None),
    status_: str | None = Query(default=None, alias="status"),
    ticket_type: str | None = Query(default=None),
    priority: str | None = Query(default=None),
    queue_id: UUID | None = Query(default=None),
    assigned_agent_id: str | None = Query(default=None),
    customer_id: str | None = Query(default=None),
    subscriber_username: str | None = Query(default=None),
    sla_status: str | None = Query(default=None),
    nms_incident_id: str | None = Query(default=None),
    search: str | None = Query(default=None),
    session: Session = Depends(db),
):
    stmt = select(Ticket).order_by(Ticket.created_at.desc())
    if tenant_id:
        stmt = stmt.where(Ticket.tenant_id == tenant_id)
    if status_:
        stmt = stmt.where(Ticket.status == status_)
    if ticket_type:
        stmt = stmt.where(Ticket.ticket_type == ticket_type)
    if priority:
        stmt = stmt.where(Ticket.priority == priority)
    if queue_id:
        stmt = stmt.where(Ticket.assigned_queue_id == queue_id)
    if assigned_agent_id:
        stmt = stmt.where(Ticket.assigned_agent_id == assigned_agent_id)
    if customer_id:
        stmt = stmt.where(Ticket.customer_id == customer_id)
    if subscriber_username:
        stmt = stmt.where(Ticket.subscriber_username == subscriber_username)
    if sla_status:
        stmt = stmt.where(Ticket.sla_status == sla_status)
    if nms_incident_id:
        stmt = stmt.where(Ticket.nms_incident_id == nms_incident_id)
    if search:
        like = f"%{search}%"
        stmt = stmt.where(
            (Ticket.ticket_number.ilike(like)) | (Ticket.subject.ilike(like)) |
            (Ticket.customer_name.ilike(like)) | (Ticket.subscriber_username.ilike(like)))
    return [serialize_ticket(t) for t in session.scalars(stmt.limit(200))]


@app.get("/api/support/tickets/{ticket_id}", dependencies=[Depends(management_auth)])
def ticket_detail(ticket_id: UUID, tenant_id: UUID | None = Query(default=None), session: Session = Depends(db)):
    try:
        ticket = _ticket_for(session, tenant_id, ticket_id)
        data = serialize_ticket(ticket)
        data["events"] = [serialize_event(e) for e in ticket_events(session, ticket.id)]
        data["watchers"] = [{"type": w.watcher_type, "id": w.watcher_id} for w in _watchers(session, ticket.id)]
        data["tags"] = [tag.tag for tag in _tags(session, ticket.id)]
        return data
    except SupportError as error:
        _raise(error)


def _ticket_for(session: Session, tenant_id, ticket_id: UUID) -> Ticket:
    if tenant_id is None:
        raise HTTPException(422, "tenant_id is required")
    return ticket_service.get_ticket_or_404(session, tenant_id, ticket_id)


def serialize_event(e: TicketEvent) -> dict:
    return {
        "id": str(e.id),
        "version": e.aggregate_version,
        "event_type": e.event_type,
        "actor_type": e.actor_type,
        "actor_id": e.actor_id,
        "correlation_id": e.correlation_id,
        "payload": e.payload,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    }


def _watchers(session: Session, ticket_id: UUID):
    from .models import TicketWatcher

    return list(session.scalars(select(TicketWatcher).where(TicketWatcher.ticket_id == ticket_id)))


def _tags(session: Session, ticket_id: UUID):
    from .models import TicketTag

    return list(session.scalars(select(TicketTag).where(TicketTag.ticket_id == ticket_id)))


def _run(session: Session, fn, request: Request):
    try:
        result = fn()
        session.commit()
        return result
    except SupportError as error:
        session.rollback()
        _raise(error)


# -- lifecycle commands -----------------------------------------------------
@app.post("/api/support/tickets/{ticket_id}/assign", dependencies=[Depends(management_auth)])
def assign_ticket(ticket_id: UUID, payload: AssignIn, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        ticket = ticket_service.assign(session, _tid(tenant_id), ticket_id, agent_id=payload.agent_id,
                                       agent_name=payload.agent_name, actor=_actor(request), reason=payload.reason,
                                       correlation_id=payload.correlation_id)
        return serialize_ticket(ticket)
    return _run(session, fn, request)


@app.post("/api/support/tickets/{ticket_id}/reassign", dependencies=[Depends(management_auth)])
def reassign_ticket(ticket_id: UUID, payload: AssignIn, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        ticket = ticket_service.reassign(session, _tid(tenant_id), ticket_id, agent_id=payload.agent_id,
                                         agent_name=payload.agent_name, actor=_actor(request), reason=payload.reason or "reassign",
                                         correlation_id=payload.correlation_id)
        return serialize_ticket(ticket)
    return _run(session, fn, request)


@app.post("/api/support/tickets/{ticket_id}/transfer", dependencies=[Depends(management_auth)])
def transfer_ticket(ticket_id: UUID, payload: TransferIn, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        ticket = ticket_service.transfer_queue(session, _tid(tenant_id), ticket_id, queue_code=payload.queue_code,
                                               actor=_actor(request), reason=payload.reason, correlation_id=payload.correlation_id)
        return serialize_ticket(ticket)
    return _run(session, fn, request)


@app.post("/api/support/tickets/{ticket_id}/accept", dependencies=[Depends(management_auth)])
def accept_ticket(ticket_id: UUID, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        return serialize_ticket(ticket_service.accept(session, _tid(tenant_id), ticket_id, actor=_actor(request)))
    return _run(session, fn, request)


@app.post("/api/support/tickets/{ticket_id}/start-work", dependencies=[Depends(management_auth)])
def start_work(ticket_id: UUID, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        return serialize_ticket(ticket_service.start_work(session, _tid(tenant_id), ticket_id, actor=_actor(request)))
    return _run(session, fn, request)


@app.post("/api/support/tickets/{ticket_id}/request-info", dependencies=[Depends(management_auth)])
def request_customer_info(ticket_id: UUID, payload: InternalNoteIn, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        ticket = ticket_service.request_customer_info(session, _tid(tenant_id), ticket_id, message=payload.body,
                                                      actor=_actor(request), correlation_id=payload.correlation_id)
        return serialize_ticket(ticket)
    return _run(session, fn, request)


@app.post("/api/support/tickets/{ticket_id}/escalate", dependencies=[Depends(management_auth)])
def escalate_ticket(ticket_id: UUID, payload: EscalateIn, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        ticket = ticket_service.escalate(session, _tid(tenant_id), ticket_id, reason=payload.reason,
                                         actor=_actor(request), correlation_id=payload.correlation_id, trigger=payload.trigger)
        return serialize_ticket(ticket)
    return _run(session, fn, request)


@app.post("/api/support/tickets/{ticket_id}/resolve", dependencies=[Depends(management_auth)])
def resolve_ticket(ticket_id: UUID, payload: ResolveIn, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        ticket = ticket_service.resolve(session, _tid(tenant_id), ticket_id, resolution_code=payload.resolution_code,
                                        summary=payload.summary, customer_explanation=payload.customer_explanation,
                                        root_cause_reference=payload.root_cause_reference,
                                        related_article_id=payload.related_article_id, actor=_actor(request),
                                        correlation_id=payload.correlation_id)
        return serialize_ticket(ticket)
    return _run(session, fn, request)


@app.post("/api/support/tickets/{ticket_id}/close", dependencies=[Depends(management_auth)])
def close_ticket(ticket_id: UUID, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        return serialize_ticket(ticket_service.close(session, _tid(tenant_id), ticket_id, actor=_actor(request)))
    return _run(session, fn, request)


@app.post("/api/support/tickets/{ticket_id}/reopen", dependencies=[Depends(management_auth)])
def reopen_ticket(ticket_id: UUID, payload: ReopenIn, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        ticket = ticket_service.reopen(session, _tid(tenant_id), ticket_id, reason=payload.reason,
                                       actor=_actor(request), correlation_id=payload.correlation_id)
        return serialize_ticket(ticket)
    return _run(session, fn, request)


@app.post("/api/support/tickets/{ticket_id}/cancel", dependencies=[Depends(management_auth)])
def cancel_ticket(ticket_id: UUID, payload: CancelIn, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        ticket = ticket_service.cancel(session, _tid(tenant_id), ticket_id, reason=payload.reason,
                                       actor=_actor(request), correlation_id=payload.correlation_id)
        return serialize_ticket(ticket)
    return _run(session, fn, request)


@app.post("/api/support/tickets/{ticket_id}/duplicate", dependencies=[Depends(management_auth)])
def duplicate_ticket(ticket_id: UUID, payload: DuplicateIn, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        ticket = ticket_service.mark_duplicate(session, _tid(tenant_id), ticket_id, original_ticket_id=payload.original_ticket_id,
                                               reason=payload.reason, actor=_actor(request), correlation_id=payload.correlation_id)
        return serialize_ticket(ticket)
    return _run(session, fn, request)


@app.post("/api/support/tickets/{ticket_id}/priority", dependencies=[Depends(management_auth)])
def change_priority(ticket_id: UUID, payload: PriorityIn, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        ticket = ticket_service.change_priority(session, _tid(tenant_id), ticket_id, priority=payload.priority,
                                                reason=payload.reason, actor=_actor(request), correlation_id=payload.correlation_id)
        return serialize_ticket(ticket)
    return _run(session, fn, request)


@app.post("/api/support/tickets/{ticket_id}/category", dependencies=[Depends(management_auth)])
def change_category(ticket_id: UUID, payload: CategoryIn, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        ticket = ticket_service.change_category(session, _tid(tenant_id), ticket_id, category_code=payload.category_code,
                                                subcategory_code=payload.subcategory_code, reason=payload.reason,
                                                actor=_actor(request), correlation_id=payload.correlation_id)
        return serialize_ticket(ticket)
    return _run(session, fn, request)


@app.get("/api/support/tickets/{ticket_id}/valid-actions", dependencies=[Depends(management_auth)])
def valid_actions(ticket_id: UUID, tenant_id: UUID | None = Query(default=None), session: Session = Depends(db)):
    from .state_machine import TICKET_TRANSITIONS

    ticket = ticket_service.get_ticket_or_404(session, _tid(tenant_id), ticket_id)
    return {"status": ticket.status, "allowed": sorted(TICKET_TRANSITIONS[ticket.status])}


@app.get("/api/support/tickets/{ticket_id}/events", dependencies=[Depends(management_auth)])
def ticket_events_endpoint(ticket_id: UUID, tenant_id: UUID | None = Query(default=None), session: Session = Depends(db)):
    ticket = ticket_service.get_ticket_or_404(session, _tid(tenant_id), ticket_id)
    return [serialize_event(e) for e in ticket_events(session, ticket.id)]


# -- comments ---------------------------------------------------------------
@app.post("/api/support/tickets/{ticket_id}/reply", dependencies=[Depends(management_auth)])
def public_reply(ticket_id: UUID, payload: CommentIn, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        ticket = ticket_service.get_ticket_or_404(session, _tid(tenant_id), ticket_id)
        comment = communication_service.add_comment(
            session, _tid(tenant_id), ticket, kind="PUBLIC_REPLY", body=payload.body, channel=payload.channel,
            sender_type="AGENT", sender_id=_actor(request), recipient_reference=payload.recipient_reference,
            correlation_id=payload.correlation_id)
        return serialize_comment(comment, include_internal=True)
    return _run(session, fn, request)


@app.post("/api/support/tickets/{ticket_id}/note", dependencies=[Depends(management_auth)])
def internal_note(ticket_id: UUID, payload: InternalNoteIn, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        ticket = ticket_service.get_ticket_or_404(session, _tid(tenant_id), ticket_id)
        comment = communication_service.add_comment(
            session, _tid(tenant_id), ticket, kind="INTERNAL_NOTE", body=payload.body, visibility="INTERNAL",
            sender_type="AGENT", sender_id=_actor(request), correlation_id=payload.correlation_id)
        return serialize_comment(comment, include_internal=True)
    return _run(session, fn, request)


@app.get("/api/support/tickets/{ticket_id}/comments", dependencies=[Depends(management_auth)])
def ticket_comments(ticket_id: UUID, tenant_id: UUID | None = Query(default=None), session: Session = Depends(db)):
    ticket = ticket_service.get_ticket_or_404(session, _tid(tenant_id), ticket_id)
    return [serialize_comment(c, include_internal=True) for c in communication_service.comments_for_ticket(session, _tid(tenant_id), ticket.id)]


@app.post("/api/support/inbound", dependencies=[Depends(internal_service_auth)])
def ingest_inbound(payload: InboundMessageIn, session: Session = Depends(db)):
    """Ingest inbound email/WhatsApp/webhook messages with threading + dedupe."""
    try:
        ticket = None
        if payload.reply_token:
            comment_ref = communication_service.ticket_by_reply_token(session, _tenant_of(payload), payload.reply_token)
            ticket = comment_ref.ticket if comment_ref else None
        elif payload.ticket_id:
            ticket = ticket_service.get_ticket_or_404(session, _tenant_of(payload), payload.ticket_id)
        elif payload.ticket_number:
            ticket = ticket_service.get_ticket_by_number(session, _tenant_of(payload), payload.ticket_number)
        if ticket is None:
            raise HTTPException(404, "ticket not found for inbound message")
        comment = communication_service.add_comment(
            session, _tenant_of(payload), ticket, kind="CUSTOMER_MESSAGE", direction="INBOUND",
            body=payload.body, channel=payload.channel, sender_type="CUSTOMER", sender_id=payload.sender_id or payload.sender_email,
            provider_message_id=payload.provider_message_id, correlation_id=payload.correlation_id)
        session.commit()
        return serialize_comment(comment, include_internal=True)
    except SupportError as error:
        session.rollback()
        _raise(error)


# -- attachments ------------------------------------------------------------
@app.post("/api/support/tickets/{ticket_id}/attachments", dependencies=[Depends(management_auth)])
async def upload_attachment(ticket_id: UUID, tenant_id: UUID | None = Query(default=None), request: Request = None,
                            file: UploadFile = File(...), session: Session = Depends(db)):
    from .services.attachment_service import store_attachment

    ticket = ticket_service.get_ticket_or_404(session, _tid(tenant_id), ticket_id)
    try:
        attachment = await store_attachment(session, _tid(tenant_id), ticket, file, uploader_type="AGENT",
                                            uploader_id=_actor(request), visibility="PUBLIC")
        session.commit()
        return {"id": str(attachment.id), "original_name": attachment.original_name, "content_type": attachment.content_type,
                "size_bytes": attachment.size_bytes, "checksum_sha256": attachment.checksum_sha256}
    except SupportError as error:
        session.rollback()
        _raise(error)


@app.get("/api/support/tickets/{ticket_id}/attachments/{attachment_id}/download", dependencies=[Depends(management_auth)])
def download_attachment(ticket_id: UUID, attachment_id: UUID, tenant_id: UUID | None = Query(default=None), session: Session = Depends(db)):
    from fastapi.responses import FileResponse

    from .services.attachment_service import load_attachment

    try:
        path, content_type = load_attachment(session, _tid(tenant_id), ticket_id, attachment_id)
        return FileResponse(path, media_type=content_type)
    except SupportError as error:
        _raise(error)


# -- watchers / related -----------------------------------------------------
@app.post("/api/support/tickets/{ticket_id}/watchers", dependencies=[Depends(management_auth)])
def add_watcher(ticket_id: UUID, payload: WatcherIn, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        return serialize_ticket(ticket_service.add_watcher(session, _tid(tenant_id), ticket_id,
                                                           watcher_type=payload.watcher_type, watcher_id=payload.watcher_id,
                                                           actor=_actor(request)))
    return _run(session, fn, request)


@app.delete("/api/support/tickets/{ticket_id}/watchers", dependencies=[Depends(management_auth)])
def remove_watcher(ticket_id: UUID, payload: WatcherIn, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        return serialize_ticket(ticket_service.remove_watcher(session, _tid(tenant_id), ticket_id,
                                                              watcher_type=payload.watcher_type, watcher_id=payload.watcher_id,
                                                              actor=_actor(request)))
    return _run(session, fn, request)


@app.post("/api/support/tickets/{ticket_id}/related", dependencies=[Depends(management_auth)])
def link_related(ticket_id: UUID, payload: RelatedIn, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        return serialize_ticket(ticket_service.link_related(session, _tid(tenant_id), ticket_id,
                                                            relation_type=payload.relation_type, to_ticket_id=payload.to_ticket_id,
                                                            actor=_actor(request)))
    return _run(session, fn, request)


# ===========================================================================
# SLA
# ===========================================================================
@app.post("/api/support/sla/policies", status_code=status.HTTP_201_CREATED, dependencies=[Depends(management_auth)])
def create_sla_policy(payload: SLAPolicyCreate, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        policy = sla_service.create_policy(session, _tid(tenant_id), code=payload.code, name=payload.name, actor=_actor(request))
        return {"id": str(policy.id), "code": policy.code, "name": policy.name}
    return _run(session, fn, request)


@app.post("/api/support/sla/policies/{policy_id}/versions", status_code=status.HTTP_201_CREATED, dependencies=[Depends(management_auth)])
def create_sla_version(policy_id: UUID, payload: SLAPolicyVersionCreate, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        version = sla_service.create_version(session, _tid(tenant_id), policy_id, definition=payload.definition,
                                             targets=[t.model_dump() for t in payload.targets],
                                             actor=_actor(request), activate=payload.activate)
        return {"id": str(version.id), "policy_id": str(policy_id), "version": version.version, "active": version.is_active}
    return _run(session, fn, request)


@app.post("/api/support/sla/policies/{policy_id}/activate", dependencies=[Depends(management_auth)])
def activate_sla_version(policy_id: UUID, payload: ActivateVersionIn, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        version = sla_service.activate_version(session, _tid(tenant_id), policy_id, payload.version, actor=_actor(request))
        return {"id": str(version.id), "version": version.version, "active": True}
    return _run(session, fn, request)


@app.get("/api/support/sla/policies", dependencies=[Depends(management_auth)])
def list_sla_policies(tenant_id: UUID | None = Query(default=None), session: Session = Depends(db)):
    from .models import SLAPolicyVersion, SLATarget

    stmt = select(SLAPolicy).where((SLAPolicy.tenant_id == tenant_id) | (SLAPolicy.tenant_id.is_(None)))
    result = []
    for policy in session.scalars(stmt.order_by(SLAPolicy.code)):
        versions = list(session.scalars(select(SLAPolicyVersion).where(SLAPolicyVersion.policy_id == policy.id)))
        targets = list(session.scalars(select(SLATarget).where(SLATarget.version_id.in_([v.id for v in versions])))) if versions else []
        result.append({
            "id": str(policy.id), "code": policy.code, "name": policy.name, "is_active": policy.is_active,
            "current_version": policy.current_version,
            "versions": [{"version": v.version, "active": v.is_active, "definition": v.definition,
                          "targets": [{"priority": t.priority, "kind": t.kind, "business_seconds": t.business_seconds} for t in targets if t.version_id == v.id]}
                         for v in versions],
        })
    return result


@app.get("/api/support/tickets/{ticket_id}/sla", dependencies=[Depends(management_auth)])
def ticket_sla(ticket_id: UUID, tenant_id: UUID | None = Query(default=None), session: Session = Depends(db)):
    ticket = ticket_service.get_ticket_or_404(session, _tid(tenant_id), ticket_id)
    sla = sla_service.get_ticket_sla(session, ticket)
    if sla is None:
        return None
    return sla_service.sla_timeline(session, sla)


@app.post("/api/support/tickets/{ticket_id}/sla/override", dependencies=[Depends(management_auth)])
def sla_override(ticket_id: UUID, payload: SLAOverrideIn, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        ticket = ticket_service.get_ticket_or_404(session, _tid(tenant_id), ticket_id)
        sla = sla_service.apply_sla_override(session, _tid(tenant_id), ticket,
                                             response_deadline=payload.response_deadline,
                                             resolution_deadline=payload.resolution_deadline,
                                             reason=payload.reason, actor=_actor(request),
                                             correlation_id=payload.correlation_id)
        ticket.response_deadline = sla.response_deadline
        ticket.resolution_deadline = sla.resolution_deadline
        return {"response_deadline": sla.response_deadline.isoformat(), "resolution_deadline": sla.resolution_deadline.isoformat()}
    return _run(session, fn, request)


@app.get("/api/support/sla/at-risk", dependencies=[Depends(management_auth)])
def sla_at_risk(tenant_id: UUID | None = Query(default=None), session: Session = Depends(db)):
    slas = list(session.scalars(select(TicketSLA).where(TicketSLA.status.in_(("AT_RISK", "BREACHED"))).limit(200)))
    result = []
    for sla in slas:
        if tenant_id and sla.tenant_id != tenant_id:
            continue
        ticket = session.get(Ticket, sla.ticket_id)
        result.append({"ticket_id": str(sla.ticket_id), "ticket_number": ticket.ticket_number if ticket else None,
                       "status": sla.status, "priority": ticket.priority if ticket else None,
                       "response_deadline": sla.response_deadline.isoformat(),
                       "resolution_deadline": sla.resolution_deadline.isoformat(),
                       "at_risk_at": sla.at_risk_at.isoformat() if sla.at_risk_at else None,
                       "breach_at": sla.breach_at.isoformat() if sla.breach_at else None})
    return result


# ===========================================================================
# Diagnostics
# ===========================================================================
@app.post("/api/support/tickets/{ticket_id}/diagnostics/refresh", dependencies=[Depends(management_auth)])
def refresh_diagnostics(ticket_id: UUID, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        ticket = ticket_service.get_ticket_or_404(session, _tid(tenant_id), ticket_id)
        snapshot = diagnostic_service.capture_diagnostic_snapshot(session, _tid(tenant_id), ticket, actor=_actor(request),
                                                                  correlation_id=correlation(None))
        return {"snapshot_id": str(snapshot.id), "status": snapshot.status, "checks": snapshot.snapshot.get("checks", [])}
    return _run(session, fn, request)


@app.get("/api/support/tickets/{ticket_id}/diagnostics", dependencies=[Depends(management_auth)])
def ticket_diagnostics(ticket_id: UUID, tenant_id: UUID | None = Query(default=None), session: Session = Depends(db)):
    ticket = ticket_service.get_ticket_or_404(session, _tid(tenant_id), ticket_id)
    snapshot = diagnostic_service.latest_snapshot(session, ticket.id)
    if snapshot is None:
        return None
    return {"snapshot_id": str(snapshot.id), "status": snapshot.status, "captured_at": snapshot.created_at.isoformat(),
            "sources": snapshot.snapshot.get("sources", {}), "checks": snapshot.snapshot.get("checks", [])}


# ===========================================================================
# Controlled support actions
# ===========================================================================
@app.post("/api/support/tickets/{ticket_id}/actions/preview", dependencies=[Depends(management_auth)])
def action_preview(ticket_id: UUID, payload: ActionRequestIn):
    return action_service.preview_action(payload.action_type, payload.payload)


@app.post("/api/support/tickets/{ticket_id}/actions", status_code=status.HTTP_201_CREATED, dependencies=[Depends(management_auth)])
def request_action(ticket_id: UUID, payload: ActionRequestIn, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        action = action_service.request_action(session, _tid(tenant_id), ticket_id, action_type=payload.action_type,
                                               payload=payload.payload, actor=_actor(request),
                                               correlation_id=payload.correlation_id, idempotency_key=payload.idempotency_key)
        return serialize_action(action)
    return _run(session, fn, request)


@app.post("/api/support/actions/{action_id}/approve", dependencies=[Depends(management_auth)])
def approve_action(action_id: UUID, payload: ApproveIn, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        action = action_service.approve_action(session, _tid(tenant_id), action_id, actor=_actor(request),
                                               reason=payload.reason, correlation_id=payload.correlation_id)
        return serialize_action(action)
    return _run(session, fn, request)


@app.post("/api/support/actions/{action_id}/execute", dependencies=[Depends(management_auth)])
def execute_action(action_id: UUID, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        action = action_service.execute_action(session, _tid(tenant_id), action_id, actor=_actor(request))
        return serialize_action(action)
    return _run(session, fn, request)


@app.post("/api/support/actions/{action_id}/retry", dependencies=[Depends(management_auth)])
def retry_action(action_id: UUID, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        action = action_service.retry_action(session, _tid(tenant_id), action_id, actor=_actor(request))
        return serialize_action(action)
    return _run(session, fn, request)


@app.post("/api/support/actions/{action_id}/cancel", dependencies=[Depends(management_auth)])
def cancel_action(action_id: UUID, payload: CancelActionIn, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        action = action_service.cancel_action(session, _tid(tenant_id), action_id, actor=_actor(request),
                                              reason=payload.reason, correlation_id=payload.correlation_id)
        return serialize_action(action)
    return _run(session, fn, request)


@app.get("/api/support/tickets/{ticket_id}/actions", dependencies=[Depends(management_auth)])
def ticket_actions(ticket_id: UUID, tenant_id: UUID | None = Query(default=None), session: Session = Depends(db)):
    ticket = ticket_service.get_ticket_or_404(session, _tid(tenant_id), ticket_id)
    actions = list(session.scalars(select(SupportAction).where(SupportAction.ticket_id == ticket.id).order_by(SupportAction.created_at)))
    return [serialize_action(a) for a in actions]


# ===========================================================================
# Outage / incident correlation
# ===========================================================================
@app.post("/api/support/tickets/{ticket_id}/incidents/suggest", dependencies=[Depends(management_auth)])
def suggest_incidents(ticket_id: UUID, tenant_id: UUID | None = Query(default=None), session: Session = Depends(db)):
    ticket = ticket_service.get_ticket_or_404(session, _tid(tenant_id), ticket_id)
    return {"suggestions": outage_service.suggest_incidents(session, _tid(tenant_id), ticket)}


@app.post("/api/support/tickets/{ticket_id}/incidents/link", dependencies=[Depends(management_auth)])
def link_incident(ticket_id: UUID, payload: LinkIncidentIn, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        ticket = outage_service.link_incident(session, _tid(tenant_id), ticket_id, incident_id=payload.incident_id,
                                              incident_number=payload.incident_number, actor=_actor(request),
                                              correlation_id=payload.correlation_id)
        return serialize_ticket(ticket)
    return _run(session, fn, request)


@app.post("/api/support/tickets/{ticket_id}/incidents/unlink", dependencies=[Depends(management_auth)])
def unlink_incident(ticket_id: UUID, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        ticket = outage_service.unlink_incident(session, _tid(tenant_id), ticket_id, actor=_actor(request))
        return serialize_ticket(ticket)
    return _run(session, fn, request)


@app.post("/api/support/tickets/{ticket_id}/orders/link", dependencies=[Depends(management_auth)])
def link_order(ticket_id: UUID, payload: LinkOrderIn, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        ticket = ticket_service.link_oss_order(session, _tid(tenant_id), ticket_id, order_id=payload.order_id,
                                               order_number=payload.order_number, actor=_actor(request),
                                               correlation_id=payload.correlation_id)
        return serialize_ticket(ticket)
    return _run(session, fn, request)


@app.post("/api/support/tickets/{ticket_id}/jobs/link", dependencies=[Depends(management_auth)])
def link_job(ticket_id: UUID, payload: LinkJobIn, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        ticket = ticket_service.link_workforce_job(session, _tid(tenant_id), ticket_id, job_id=payload.job_id,
                                                   job_number=payload.job_number, actor=_actor(request),
                                                   correlation_id=payload.correlation_id)
        return serialize_ticket(ticket)
    return _run(session, fn, request)


@app.post("/api/support/tickets/{ticket_id}/disputes/link", dependencies=[Depends(management_auth)])
def link_dispute(ticket_id: UUID, payload: LinkDisputeIn, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        ticket = ticket_service.link_billing_dispute(session, _tid(tenant_id), ticket_id, dispute_id=payload.dispute_id,
                                                     actor=_actor(request), correlation_id=payload.correlation_id)
        return serialize_ticket(ticket)
    return _run(session, fn, request)


# ===========================================================================
# CSAT
# ===========================================================================
@app.get("/api/support/csat", dependencies=[Depends(management_auth)])
def list_csat(tenant_id: UUID | None = Query(default=None), session: Session = Depends(db)):
    stmt = select(CustomerSatisfaction).order_by(CustomerSatisfaction.submitted_at.desc())
    if tenant_id:
        stmt = stmt.where(CustomerSatisfaction.tenant_id == tenant_id)
    return [{"id": str(c.id), "ticket_id": str(c.ticket_id), "rating": c.rating, "channel": c.channel,
             "comment": c.comment, "submitted_at": c.submitted_at.isoformat(), "low_score_reviewed": c.low_score_reviewed}
            for c in session.scalars(stmt.limit(200))]


# ===========================================================================
# Knowledge
# ===========================================================================
@app.post("/api/support/knowledge", status_code=status.HTTP_201_CREATED, dependencies=[Depends(management_auth)])
def create_article(payload: ArticleCreate, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    from .models import TicketCategory

    category = None
    if payload.category_code:
        category = session.scalars(select(TicketCategory).where(TicketCategory.code == payload.category_code.upper())).first()
    def fn():
        article = knowledge_service.create_article(
            session, _tid(tenant_id), slug=payload.slug, title=payload.title, body=payload.body,
            category_id=category.id if category else None, visibility=payload.visibility, status=payload.status,
            tags=payload.tags, author=_actor(request))
        return {"id": str(article.id), "slug": article.slug, "title": article.title, "status": article.status, "version": article.version}
    return _run(session, fn, request)


@app.put("/api/support/knowledge/{article_id}", dependencies=[Depends(management_auth)])
def update_article(article_id: UUID, payload: ArticleUpdate, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        article = knowledge_service.update_article(session, _tid(tenant_id), article_id, title=payload.title,
                                                   body=payload.body, tags=payload.tags, actor=_actor(request))
        return {"id": str(article.id), "version": article.version, "status": article.status}
    return _run(session, fn, request)


@app.post("/api/support/knowledge/{article_id}/publish", dependencies=[Depends(management_auth)])
def publish_article(article_id: UUID, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        article = knowledge_service.publish_article(session, _tid(tenant_id), article_id, actor=_actor(request))
        return {"id": str(article.id), "status": article.status}
    return _run(session, fn, request)


@app.get("/api/support/knowledge", dependencies=[Depends(management_auth)])
def search_articles(query: str | None = Query(default=None), tenant_id: UUID | None = Query(default=None), session: Session = Depends(db)):
    tenant_id = _tid(tenant_id)
    articles = knowledge_service.search_articles(session, tenant_id, query=query)
    return [{"id": str(a.id), "slug": a.slug, "title": a.title, "visibility": a.visibility, "status": a.status,
             "version": a.version, "usage_count": a.usage_count} for a in articles]


@app.post("/api/support/tickets/{ticket_id}/knowledge/suggest", dependencies=[Depends(management_auth)])
def suggest_articles(ticket_id: UUID, tenant_id: UUID | None = Query(default=None), session: Session = Depends(db)):
    ticket = ticket_service.get_ticket_or_404(session, _tid(tenant_id), ticket_id)
    return {"suggestions": knowledge_service.suggest_for_ticket(session, _tid(tenant_id), ticket)}


@app.post("/api/support/knowledge/{article_id}/usage", dependencies=[Depends(management_auth)])
def record_article_usage(article_id: UUID, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        knowledge_service.record_usage(session, _tid(tenant_id), article_id, used_by=_actor(request))
        return {"ok": True}
    return _run(session, fn, request)


# ===========================================================================
# Catalog / queues / teams / agents / routing
# ===========================================================================
@app.get("/api/support/catalog", dependencies=[Depends(management_auth)])
def catalog(tenant_id: UUID | None = Query(default=None), session: Session = Depends(db)):
    tenant_id = _tid(tenant_id)
    catalog_service.ensure_tenant_defaults(session, tenant_id)
    from .models import TicketCategory, TicketQueue, SupportTeam, TicketSubcategory, TicketType

    types = [{"code": t.code, "name": t.name} for t in session.scalars(
        select(TicketType).where((TicketType.tenant_id == tenant_id) | (TicketType.tenant_id.is_(None))))]
    categories = []
    for cat in session.scalars(select(TicketCategory).where((TicketCategory.tenant_id == tenant_id) | (TicketCategory.tenant_id.is_(None)))):
        subs = [{"code": s.code, "name": s.name} for s in session.scalars(
            select(TicketSubcategory).where(TicketSubcategory.category_id == cat.id))]
        categories.append({"code": cat.code, "name": cat.name, "subcategories": subs})
    queues = [{"code": q.code, "name": q.name, "type": q.queue_type} for q in session.scalars(
        select(TicketQueue).where(TicketQueue.tenant_id == tenant_id))]
    teams = [{"code": t.code, "name": t.name} for t in session.scalars(
        select(SupportTeam).where(SupportTeam.tenant_id == tenant_id))]
    return {"types": types, "categories": categories, "queues": queues, "teams": teams}


@app.post("/api/support/agents", status_code=status.HTTP_201_CREATED, dependencies=[Depends(management_auth)])
def add_agent(payload: AgentIn, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        membership = catalog_service.add_agent(session, _tid(tenant_id), payload.team_code, payload.agent_id,
                                               name=payload.name, role=payload.role, skills=payload.skills,
                                               locations=payload.locations, actor=_actor(request))
        return {"id": str(membership.id), "team_id": str(membership.team_id), "agent_id": membership.agent_id, "role": membership.role}
    return _run(session, fn, request)


@app.post("/api/support/routing", status_code=status.HTTP_201_CREATED, dependencies=[Depends(management_auth)])
def add_routing_rule(payload: RoutingRuleIn, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        rule = catalog_service.add_routing_rule(session, _tid(tenant_id), name=payload.name,
                                                target_queue_code=payload.target_queue_code, ticket_type=payload.ticket_type,
                                                category_code=payload.category_code, strategy=payload.strategy,
                                                fallback_queue_code=payload.fallback_queue_code,
                                                required_skills=payload.required_skills, priority=payload.priority,
                                                actor=_actor(request))
        return {"id": str(rule.id), "name": rule.name, "strategy": rule.strategy}
    return _run(session, fn, request)


@app.get("/api/support/routing", dependencies=[Depends(management_auth)])
def list_routing_rules(tenant_id: UUID | None = Query(default=None), session: Session = Depends(db)):
    tenant_id = _tid(tenant_id)
    from .models import RoutingRule

    rules = list(session.scalars(select(RoutingRule).where(RoutingRule.tenant_id == tenant_id).order_by(RoutingRule.priority)))
    return [{"id": str(r.id), "name": r.name, "ticket_type": r.ticket_type, "strategy": r.strategy,
             "fallback_queue_id": str(r.fallback_queue_id) if r.fallback_queue_id else None, "priority": r.priority} for r in rules]


# ===========================================================================
# Reports / audit
# ===========================================================================
@app.get("/api/support/reports/overview", dependencies=[Depends(management_auth)])
def report_overview(tenant_id: UUID | None = Query(default=None), session: Session = Depends(db)):
    tenant_id = _tid(tenant_id)
    from .enums import TICKET_STATES

    open_states = tuple(s for s in TICKET_STATES if s not in ("CLOSED", "CANCELLED", "DUPLICATE", "RESOLVED"))
    return {
        "open_tickets": session.scalar(select(func.count(Ticket.id)).where(Ticket.tenant_id == tenant_id, Ticket.status.in_(open_states))) or 0,
        "unassigned": session.scalar(select(func.count(Ticket.id)).where(Ticket.tenant_id == tenant_id, Ticket.assigned_agent_id.is_(None), Ticket.status.in_(open_states))) or 0,
        "at_risk": session.scalar(select(func.count(TicketSLA.id)).where(TicketSLA.tenant_id == tenant_id, TicketSLA.status == "AT_RISK")) or 0,
        "breached": session.scalar(select(func.count(TicketSLA.id)).where(TicketSLA.tenant_id == tenant_id, TicketSLA.status == "BREACHED")) or 0,
        "reopened": session.scalar(select(func.count(Ticket.id)).where(Ticket.tenant_id == tenant_id, Ticket.reopened_count > 0)) or 0,
        "csat_count": session.scalar(select(func.count(CustomerSatisfaction.id)).where(CustomerSatisfaction.tenant_id == tenant_id)) or 0,
    }


@app.get("/api/support/reports/tickets", dependencies=[Depends(management_auth)])
def report_tickets(tenant_id: UUID | None = Query(default=None), session: Session = Depends(db)):
    tenant_id = _tid(tenant_id)
    rows = session.execute(
        select(Ticket.status, func.count(Ticket.id)).where(Ticket.tenant_id == tenant_id).group_by(Ticket.status)
    ).all()
    return {"by_status": {status: count for status, count in rows}}


@app.get("/api/support/audit", dependencies=[Depends(management_auth)])
def audit_log(tenant_id: UUID | None = Query(default=None), session: Session = Depends(db)):
    tenant_id = _tid(tenant_id)
    from .models import AuditLog

    stmt = select(AuditLog).order_by(AuditLog.created_at.desc())
    if tenant_id:
        stmt = stmt.where(AuditLog.tenant_id == tenant_id)
    return [{"id": str(a.id), "event_type": a.event_type, "entity_type": a.entity_type, "entity_id": a.entity_id,
             "actor": a.actor, "reason": a.reason, "created_at": a.created_at.isoformat() if a.created_at else None}
            for a in session.scalars(stmt.limit(200))]


# ===========================================================================
# Customer portal
# ===========================================================================
def _portal(request: Request) -> dict:
    return portal_principal(request)


def _portal_tenant(request: Request) -> UUID:
    return UUID(portal_principal(request)["tenant_id"])


def _portal_customer(request: Request) -> str:
    return portal_principal(request)["customer_id"]


@app.get("/api/support/portal/me", dependencies=[Depends(customer_auth)])
def portal_me(request: Request):
    return _portal(request)


@app.post("/api/support/portal/tickets", status_code=status.HTTP_201_CREATED, dependencies=[Depends(customer_auth)])
def portal_create_ticket(payload: TicketCreate, request: Request, session: Session = Depends(db)):
    if payload.customer_id and payload.customer_id != _portal_customer(request):
        raise HTTPException(403, "cannot create a ticket for another customer")
    try:
        ticket = ticket_service.create_ticket(
            session, _portal_tenant(request),
            customer_id=_portal_customer(request),
            customer_number=payload.customer_number, customer_name=payload.customer_name,
            customer_tier=payload.customer_tier,
            service_subscription_id=payload.service_subscription_id,
            subscriber_username=payload.subscriber_username,
            service_location_id=payload.service_location_id,
            billing_account_id=payload.billing_account_id,
            franchise_id=payload.franchise_id, reseller_id=payload.reseller_id,
            category_code=payload.category_code, subcategory_code=payload.subcategory_code,
            source_channel=payload.source_channel, impact=payload.impact, urgency=payload.urgency,
            priority=payload.priority, ticket_type=payload.ticket_type, subject=payload.subject,
            description=payload.description, correlation_id=payload.correlation_id,
            idempotency_key=payload.idempotency_key, extra_tags=payload.tags,
            actor=_portal_customer(request), actor_type="customer",
        )
        session.commit()
        session.refresh(ticket)
        return serialize_ticket(ticket, include_internal=False)
    except SupportError as error:
        session.rollback()
        _raise(error)


@app.get("/api/support/portal/tickets", dependencies=[Depends(customer_auth)])
def portal_list_tickets(request: Request, session: Session = Depends(db)):
    tenant = _portal_tenant(request)
    customer = _portal_customer(request)
    tickets = list(session.scalars(
        select(Ticket).where(Ticket.tenant_id == tenant, Ticket.customer_id == customer).order_by(Ticket.created_at.desc())))
    return [serialize_ticket(t, include_internal=False) for t in tickets]


def _portal_ticket(session: Session, request: Request, ticket_id: UUID) -> Ticket:
    tenant = _portal_tenant(request)
    customer = _portal_customer(request)
    ticket = ticket_service.get_ticket_or_404(session, tenant, ticket_id)
    if ticket.customer_id != customer:
        raise HTTPException(403, "not your ticket")
    return ticket


@app.get("/api/support/portal/tickets/{ticket_id}", dependencies=[Depends(customer_auth)])
def portal_ticket_detail(ticket_id: UUID, request: Request, session: Session = Depends(db)):
    ticket = _portal_ticket(session, request, ticket_id)
    data = serialize_ticket(ticket, include_internal=False)
    # Safe timeline: public comments only, no internal notes or staff details.
    comments = communication_service.comments_for_ticket(session, ticket.tenant_id, ticket.id, include_internal=False)
    data["timeline"] = [serialize_comment(c, include_internal=False) for c in comments]
    data["expected_response"] = ticket.response_deadline
    data["expected_resolution"] = ticket.resolution_deadline
    if ticket.workforce_job_number:
        data["scheduled_visit"] = {"job_number": ticket.workforce_job_number, "status": ticket.status}
    return data


@app.post("/api/support/portal/tickets/{ticket_id}/reply", dependencies=[Depends(customer_auth)])
def portal_reply(ticket_id: UUID, payload: CommentIn, request: Request, session: Session = Depends(db)):
    def fn():
        ticket = _portal_ticket(session, request, ticket_id)
        if ticket.status == "CLOSED":
            raise HTTPException(409, "closed tickets require reopening before replying")
        comment = communication_service.add_comment(
            session, ticket.tenant_id, ticket, kind="CUSTOMER_MESSAGE", direction="INBOUND",
            body=payload.body, channel=payload.channel, sender_type="CUSTOMER",
            sender_id=_portal_customer(request), provider_message_id=payload.provider_message_id,
            correlation_id=payload.correlation_id)
        if ticket.status == "PENDING_CUSTOMER":
            ticket_service.accept(session, ticket.tenant_id, ticket.id, actor=_portal_customer(request))
        return serialize_comment(comment, include_internal=False)
    try:
        result = fn()
        session.commit()
        return result
    except SupportError as error:
        session.rollback()
        _raise(error)


@app.post("/api/support/portal/tickets/{ticket_id}/confirm", dependencies=[Depends(customer_auth)])
def portal_confirm(ticket_id: UUID, request: Request, session: Session = Depends(db)):
    def fn():
        ticket = _portal_ticket(session, request, ticket_id)
        if ticket.status != "RESOLVED":
            raise HTTPException(409, "only resolved tickets can be confirmed")
        return serialize_ticket(ticket_service.close(session, ticket.tenant_id, ticket.id, actor=_portal_customer(request)))
    try:
        result = fn()
        session.commit()
        return result
    except SupportError as error:
        session.rollback()
        _raise(error)


@app.post("/api/support/portal/tickets/{ticket_id}/reopen", dependencies=[Depends(customer_auth)])
def portal_reopen(ticket_id: UUID, payload: ReopenIn, request: Request, session: Session = Depends(db)):
    def fn():
        ticket = _portal_ticket(session, request, ticket_id)
        return serialize_ticket(ticket_service.reopen(session, ticket.tenant_id, ticket.id, reason=payload.reason,
                                                      actor=_portal_customer(request), correlation_id=payload.correlation_id))
    try:
        result = fn()
        session.commit()
        return result
    except SupportError as error:
        session.rollback()
        _raise(error)


@app.post("/api/support/portal/tickets/{ticket_id}/csat", dependencies=[Depends(customer_auth)])
def portal_csat(ticket_id: UUID, payload: CSATIn, request: Request, session: Session = Depends(db)):
    def fn():
        ticket = _portal_ticket(session, request, ticket_id)
        if not ticket.csat_eligible:
            raise HTTPException(409, "CSAT is not open for this ticket")
        return ticket_service.submit_csat(session, ticket.tenant_id, ticket.id, rating=payload.rating,
                                          comment=payload.comment, channel=payload.channel)
    try:
        result = fn()
        session.commit()
        return result
    except SupportError as error:
        session.rollback()
        _raise(error)


def _tid(tenant_id: UUID | None) -> UUID:
    """Resolve the tenant scope for a management request.

    The JWT principal (validated by management_auth) is authoritative; any
    client-supplied tenant_id is only accepted when it matches the principal."""
    from .security import current_tenant

    principal_tenant = current_tenant.get()
    if tenant_id is not None:
        if principal_tenant and not secrets.compare_digest(str(tenant_id), str(principal_tenant)):
            raise HTTPException(403, "tenant access denied")
        return tenant_id
    if principal_tenant:
        return UUID(principal_tenant)
    raise HTTPException(422, "tenant_id is required")


@app.get("/api/support/tickets/{ticket_id}/billing-context", dependencies=[Depends(management_auth)])
def billing_context(ticket_id: UUID, tenant_id: UUID | None = Query(default=None), session: Session = Depends(db)):
    """Billing summary for authorized support roles. Full payment detail is only
    included for callers with the explicit permission."""
    ticket = ticket_service.get_ticket_or_404(session, _tid(tenant_id), ticket_id)
    from .integrations.base import get_adapter

    result = get_adapter("bss").get_billing_context(ticket.billing_account_id, ticket.customer_id, include_payment_detail=False)
    if result.ok:
        return {"status": "COMPLETE", "billing": result.output}
    return {"status": "FAILED", "error_code": result.error_code, "error_detail": result.error_detail}
