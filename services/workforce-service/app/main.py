"""Workforce Service — field operations, work orders, dispatch, technician
mobile, QA, field SLA and proof of work (Milestone 6).

Explicit command endpoints only; no arbitrary status PATCHes. Technician and
customer surfaces return only the data their role needs."""
from contextlib import asynccontextmanager
import secrets
from datetime import date as _date
from os import getenv
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, Request, UploadFile, File, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import models  # noqa: F401
from .database import Base, SessionLocal, engine
from .domain.exceptions import WorkforceError
from .models import (
    Appointment,
    FieldSLAInstance,
    TechnicianProfile,
    WorkOrder,
    WorkOrderEvent,
)
from .schemas import (
    AcknowledgementIn,
    ActivateVersionIn,
    AssignIn,
    AvailabilityIn,
    BlockIn,
    CertExceptionIn,
    CertificationIn,
    CheckInIn,
    ChecklistSubmitIn,
    CompleteIn,
    DeviceIn,
    LinkIncidentIn,
    LinkOrderIn,
    LinkTicketIn,
    MaterialIn,
    OfflineSyncIn,
    PartsIn,
    PlanSequenceIn,
    ProofIn,
    ReasonIn,
    RelatedIn,
    ReviewIn,
    ScheduleIn,
    ShiftIn,
    SLAExceptionIn,
    SLAPolicyCreate,
    SLAPolicyVersionCreate,
    SkillIn,
    StatusIn,
    TechnicianCreate,
    ValidateAssignIn,
    WorkOrderCreate,
)
from .security import (
    customer_auth,
    customer_principal,
    internal_service_auth,
    management_auth,
    technician_auth,
    technician_principal,
)
from .services import (
    appointment_service,
    catalog_service,
    checklist_service,
    dispatch_service,
    inventory_service,
    offline_service,
    proof_service,
    qa_service,
    sla_service,
    technician_service,
    workorder_service,
)
from .services.audit_service import correlation, work_order_events


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


app = FastAPI(title="Workforce Service", version="6.0.0", lifespan=lifespan)


def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _raise(error: Exception) -> None:
    if isinstance(error, WorkforceError):
        raise HTTPException(error.status_code, {"code": error.code, "detail": error.message}) from error
    raise HTTPException(422, str(error)) from error


def _actor(request: Request) -> str:
    principal = getattr(request.state, "workforce_principal", None)
    if principal:
        return principal["subject"]
    return "system"


def _tid(tenant_id: UUID | None) -> UUID:
    from .security import current_tenant

    principal_tenant = current_tenant.get()
    if tenant_id is not None:
        if principal_tenant and not secrets.compare_digest(str(tenant_id), str(principal_tenant)):
            raise HTTPException(403, "tenant access denied")
        return tenant_id
    if principal_tenant:
        return UUID(principal_tenant)
    raise HTTPException(422, "tenant_id is required")


def _run(session: Session, fn, request: Request):
    try:
        result = fn()
        session.commit()
        return result
    except WorkforceError as error:
        session.rollback()
        _raise(error)


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------
def serialize_work_order(wo: WorkOrder, *, include_internal: bool = True) -> dict:
    return {
        "id": str(wo.id),
        "work_order_number": wo.work_order_number,
        "work_order_type": wo.work_order_type,
        "priority": wo.priority,
        "severity": wo.severity,
        "status": wo.status,
        "dispatch_state": wo.dispatch_state,
        "source_channel": wo.source_channel,
        "customer_id": wo.customer_id,
        "customer_name": wo.customer_name,
        "service_subscription_id": wo.service_subscription_id,
        "service_location_id": wo.service_location_id,
        "oss_order_id": wo.oss_order_id,
        "oss_order_number": wo.oss_order_number,
        "support_ticket_id": wo.support_ticket_id,
        "support_ticket_number": wo.support_ticket_number,
        "nms_incident_id": wo.nms_incident_id,
        "assigned_technician_id": str(wo.assigned_technician_id) if wo.assigned_technician_id else None,
        "assigned_technician_name": wo.assigned_technician_name if include_internal else None,
        "scheduled_start": wo.scheduled_start.isoformat() if wo.scheduled_start else None,
        "scheduled_end": wo.scheduled_end.isoformat() if wo.scheduled_end else None,
        "field_sla_status": wo.field_sla_status,
        "arrival_deadline": wo.arrival_deadline.isoformat() if wo.arrival_deadline else None,
        "completion_deadline": wo.completion_deadline.isoformat() if wo.completion_deadline else None,
        "latitude": wo.latitude,
        "longitude": wo.longitude,
        "address_line": wo.address_line,
        "instructions": wo.instructions,
        "result_code": wo.result_code,
        "result_summary": wo.result_summary if include_internal else None,
        "created_at": wo.created_at.isoformat() if wo.created_at else None,
        "completed_at": wo.completed_at.isoformat() if wo.completed_at else None,
    }


def serialize_appointment(a: Appointment) -> dict:
    return {"id": str(a.id), "work_order_id": str(a.work_order_id), "window_start": a.window_start.isoformat(),
            "window_end": a.window_end.isoformat(), "status": a.status, "attempt_number": a.attempt_number,
            "customer_preferred": a.customer_preferred,
            "confirmed_at": a.confirmed_at.isoformat() if a.confirmed_at else None}


def serialize_event(e: WorkOrderEvent) -> dict:
    return {"id": str(e.id), "version": e.aggregate_version, "event_type": e.event_type,
            "actor_type": e.actor_type, "actor_id": e.actor_id, "payload": e.payload,
            "created_at": e.created_at.isoformat() if e.created_at else None}


def _wo_or_404(session: Session, tenant_id, work_order_id: UUID) -> WorkOrder:
    return workorder_service.get_work_order_or_404(session, tenant_id, work_order_id)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok", "service": getenv("SERVICE_NAME", "workforce-service")}


@app.get("/status")
def service_status():
    return {"service": "workforce", "phase": "milestone-6-field-workforce-management"}


# ===========================================================================
# Work orders — management
# ===========================================================================
@app.post("/api/workforce/work-orders", status_code=status.HTTP_201_CREATED, dependencies=[Depends(management_auth)])
def create_work_order(payload: WorkOrderCreate, request: Request, session: Session = Depends(db)):
    tenant_id = _tid(payload.tenant_id)
    catalog_service.ensure_tenant_defaults(session, tenant_id)
    try:
        wo = workorder_service.create_work_order(
            session, tenant_id,
            work_order_type=payload.work_order_type, customer_id=payload.customer_id,
            customer_name=payload.customer_name, service_subscription_id=payload.service_subscription_id,
            service_location_id=payload.service_location_id, oss_order_id=payload.oss_order_id,
            oss_order_number=payload.oss_order_number, support_ticket_id=payload.support_ticket_id,
            support_ticket_number=payload.support_ticket_number, nms_incident_id=payload.nms_incident_id,
            billing_ref=payload.billing_ref, franchise_id=payload.franchise_id, reseller_id=payload.reseller_id,
            branch_id=payload.branch_id, service_area_id=payload.service_area_id, priority=payload.priority,
            severity=payload.severity, latitude=payload.latitude, longitude=payload.longitude,
            address_line=payload.address_line, scheduled_start=payload.scheduled_start,
            scheduled_end=payload.scheduled_end, instructions=payload.instructions,
            source_channel=payload.source_channel, strategy=payload.strategy,
            correlation_id=payload.correlation_id, idempotency_key=payload.idempotency_key,
            actor=_actor(request), actor_type="agent")
        session.commit()
        session.refresh(wo)
        return serialize_work_order(wo)
    except WorkforceError as error:
        _raise(error)


@app.get("/api/workforce/work-orders", dependencies=[Depends(management_auth)])
def list_work_orders(
    tenant_id: UUID | None = Query(default=None),
    status_: str | None = Query(default=None, alias="status"),
    work_order_type: str | None = Query(default=None),
    priority: str | None = Query(default=None),
    technician_id: UUID | None = Query(default=None),
    customer_id: str | None = Query(default=None),
    service_location_id: str | None = Query(default=None),
    oss_order_id: str | None = Query(default=None),
    support_ticket_id: str | None = Query(default=None),
    sla_status: str | None = Query(default=None),
    search: str | None = Query(default=None),
    session: Session = Depends(db),
):
    tenant_id = _tid(tenant_id)
    stmt = select(WorkOrder).where(WorkOrder.tenant_id == tenant_id).order_by(WorkOrder.created_at.desc())
    if status_:
        stmt = stmt.where(WorkOrder.status == status_)
    if work_order_type:
        stmt = stmt.where(WorkOrder.work_order_type == work_order_type)
    if priority:
        stmt = stmt.where(WorkOrder.priority == priority)
    if technician_id:
        stmt = stmt.where(WorkOrder.assigned_technician_id == technician_id)
    if customer_id:
        stmt = stmt.where(WorkOrder.customer_id == customer_id)
    if service_location_id:
        stmt = stmt.where(WorkOrder.service_location_id == service_location_id)
    if oss_order_id:
        stmt = stmt.where(WorkOrder.oss_order_id == oss_order_id)
    if support_ticket_id:
        stmt = stmt.where(WorkOrder.support_ticket_id == support_ticket_id)
    if sla_status:
        stmt = stmt.where(WorkOrder.field_sla_status == sla_status)
    if search:
        like = f"%{search}%"
        stmt = stmt.where((WorkOrder.work_order_number.ilike(like)) | (WorkOrder.customer_name.ilike(like))
                          | (WorkOrder.support_ticket_number.ilike(like)) | (WorkOrder.oss_order_number.ilike(like)))
    return [serialize_work_order(wo) for wo in session.scalars(stmt.limit(200))]


@app.get("/api/workforce/work-orders/{work_order_id}", dependencies=[Depends(management_auth)])
def work_order_detail(work_order_id: UUID, tenant_id: UUID | None = Query(default=None), session: Session = Depends(db)):
    tenant = _tid(tenant_id)
    wo = _wo_or_404(session, tenant, work_order_id)
    data = serialize_work_order(wo)
    data["events"] = [serialize_event(e) for e in work_order_events(session, wo.id)]
    data["appointments"] = [serialize_appointment(a) for a in session.scalars(
        select(Appointment).where(Appointment.work_order_id == wo.id).order_by(Appointment.attempt_number))]
    data["proof"] = [{"id": str(p.id), "evidence_type": p.evidence_type, "verification_state": p.verification_state}
                     for p in proof_service.proofs_for_work_order(session, tenant, wo.id)]
    data["checklist"] = wo.checklist_snapshot
    return data


@app.get("/api/workforce/work-orders/{work_order_id}/valid-actions", dependencies=[Depends(management_auth)])
def valid_actions(work_order_id: UUID, tenant_id: UUID | None = Query(default=None), session: Session = Depends(db)):
    wo = _wo_or_404(session, _tid(tenant_id), work_order_id)
    return {"status": wo.status, "allowed": workorder_service.valid_actions(wo)}


@app.get("/api/workforce/work-orders/{work_order_id}/events", dependencies=[Depends(management_auth)])
def events(work_order_id: UUID, tenant_id: UUID | None = Query(default=None), session: Session = Depends(db)):
    wo = _wo_or_404(session, _tid(tenant_id), work_order_id)
    return [serialize_event(e) for e in work_order_events(session, wo.id)]


# -- lifecycle commands -----------------------------------------------------
@app.post("/api/workforce/work-orders/{work_order_id}/validate", dependencies=[Depends(management_auth)])
def validate_wo(work_order_id: UUID, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        return serialize_work_order(workorder_service.validate_work_order(session, _tid(tenant_id), work_order_id, actor=_actor(request)))
    return _run(session, fn, request)


@app.post("/api/workforce/work-orders/{work_order_id}/schedule", dependencies=[Depends(management_auth)])
def schedule_wo(work_order_id: UUID, payload: ScheduleIn, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        wo = _wo_or_404(session, _tid(tenant_id), work_order_id)
        appointment = appointment_service.schedule(session, _tid(tenant_id), wo, window_start=payload.window_start,
                                                   window_end=payload.window_end, customer_preferred=payload.customer_preferred,
                                                   actor=_actor(request), correlation_id=payload.correlation_id)
        return {"appointment": serialize_appointment(appointment), "work_order": serialize_work_order(wo)}
    return _run(session, fn, request)


@app.post("/api/workforce/work-orders/{work_order_id}/reschedule", dependencies=[Depends(management_auth)])
def reschedule_wo(work_order_id: UUID, payload: ScheduleIn, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        wo = _wo_or_404(session, _tid(tenant_id), work_order_id)
        if wo.current_appointment_id is None:
            raise HTTPException(422, "no appointment to reschedule")
        appointment = appointment_service.reschedule(session, _tid(tenant_id), wo.current_appointment_id,
                                                     window_start=payload.window_start, window_end=payload.window_end,
                                                     reason="reschedule requested", actor=_actor(request),
                                                     correlation_id=payload.correlation_id)
        return {"appointment": serialize_appointment(appointment), "work_order": serialize_work_order(wo)}
    return _run(session, fn, request)


@app.post("/api/workforce/work-orders/{work_order_id}/assign", dependencies=[Depends(management_auth)])
def assign_wo(work_order_id: UUID, payload: AssignIn, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        wo = workorder_service.assign_work_order(session, _tid(tenant_id), work_order_id, strategy=payload.strategy,
                                                 technician_id=payload.technician_id, reason=payload.reason,
                                                 actor=_actor(request), correlation_id=payload.correlation_id)
        return serialize_work_order(wo)
    return _run(session, fn, request)


@app.post("/api/workforce/work-orders/{work_order_id}/reassign", dependencies=[Depends(management_auth)])
def reassign_wo(work_order_id: UUID, payload: AssignIn, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        if not payload.reason:
            raise HTTPException(422, "reassignment requires a reason")
        wo = workorder_service.assign_work_order(session, _tid(tenant_id), work_order_id, strategy=payload.strategy,
                                                 technician_id=payload.technician_id, reason=payload.reason,
                                                 actor=_actor(request), correlation_id=payload.correlation_id)
        return serialize_work_order(wo)
    return _run(session, fn, request)


@app.post("/api/workforce/work-orders/{work_order_id}/dispatch", dependencies=[Depends(management_auth)])
def dispatch_wo(work_order_id: UUID, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        return serialize_work_order(workorder_service.dispatch_work_order(session, _tid(tenant_id), work_order_id, actor=_actor(request)))
    return _run(session, fn, request)


@app.post("/api/workforce/work-orders/{work_order_id}/cancel", dependencies=[Depends(management_auth)])
def cancel_wo(work_order_id: UUID, payload: ReasonIn, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        return serialize_work_order(workorder_service.cancel_work_order(session, _tid(tenant_id), work_order_id,
                                                                        reason=payload.reason, actor=_actor(request),
                                                                        correlation_id=payload.correlation_id))
    return _run(session, fn, request)


@app.post("/api/workforce/work-orders/{work_order_id}/fail", dependencies=[Depends(management_auth)])
def fail_wo(work_order_id: UUID, payload: ReasonIn, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        return serialize_work_order(workorder_service.fail_work_order(session, _tid(tenant_id), work_order_id,
                                                                      reason=payload.reason, actor=_actor(request),
                                                                      correlation_id=payload.correlation_id))
    return _run(session, fn, request)


@app.post("/api/workforce/work-orders/{work_order_id}/complete", dependencies=[Depends(management_auth)])
def complete_wo(work_order_id: UUID, payload: CompleteIn, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        return serialize_work_order(workorder_service.complete_work_order(
            session, _tid(tenant_id), work_order_id, result_code=payload.result_code, summary=payload.summary,
            root_cause_reference=payload.root_cause_reference, actor=_actor(request),
            correlation_id=payload.correlation_id))
    return _run(session, fn, request)


# -- links ------------------------------------------------------------------
@app.post("/api/workforce/work-orders/{work_order_id}/link-order", dependencies=[Depends(management_auth)])
def link_order(work_order_id: UUID, payload: LinkOrderIn, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        return serialize_work_order(workorder_service.link_oss_order(session, _tid(tenant_id), work_order_id,
                                                                     order_id=payload.order_id, order_number=payload.order_number,
                                                                     actor=_actor(request), correlation_id=payload.correlation_id))
    return _run(session, fn, request)


@app.post("/api/workforce/work-orders/{work_order_id}/link-ticket", dependencies=[Depends(management_auth)])
def link_ticket(work_order_id: UUID, payload: LinkTicketIn, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        return serialize_work_order(workorder_service.link_ticket(session, _tid(tenant_id), work_order_id,
                                                                  ticket_id=payload.ticket_id, ticket_number=payload.ticket_number,
                                                                  actor=_actor(request), correlation_id=payload.correlation_id))
    return _run(session, fn, request)


@app.post("/api/workforce/work-orders/{work_order_id}/link-incident", dependencies=[Depends(management_auth)])
def link_incident(work_order_id: UUID, payload: LinkIncidentIn, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        return serialize_work_order(workorder_service.link_incident(session, _tid(tenant_id), work_order_id,
                                                                    incident_id=payload.incident_id, actor=_actor(request),
                                                                    correlation_id=payload.correlation_id))
    return _run(session, fn, request)


@app.post("/api/workforce/work-orders/{work_order_id}/related", dependencies=[Depends(management_auth)])
def related(work_order_id: UUID, payload: RelatedIn, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        return serialize_work_order(workorder_service.link_related(session, _tid(tenant_id), work_order_id,
                                                                   relation_type=payload.relation_type,
                                                                   to_work_order_id=payload.to_work_order_id,
                                                                   actor=_actor(request)))
    return _run(session, fn, request)


# ===========================================================================
# QA
# ===========================================================================
@app.get("/api/workforce/qa/pending", dependencies=[Depends(management_auth)])
def qa_pending(tenant_id: UUID | None = Query(default=None), session: Session = Depends(db)):
    tenant_id = _tid(tenant_id)
    reviews = qa_service.pending_reviews(session, tenant_id)
    return [{"id": str(r.id), "work_order_id": str(r.work_order_id), "state": r.state,
             "created_at": r.created_at.isoformat() if r.created_at else None} for r in reviews]


@app.get("/api/workforce/work-orders/{work_order_id}/proof", dependencies=[Depends(management_auth)])
def work_order_proof(work_order_id: UUID, tenant_id: UUID | None = Query(default=None), session: Session = Depends(db)):
    tenant = _tid(tenant_id)
    wo = _wo_or_404(session, tenant, work_order_id)
    return [{"id": str(p.id), "evidence_type": p.evidence_type, "verification_state": p.verification_state,
             "checksum": p.checksum, "capture_timestamp": p.capture_timestamp.isoformat() if p.capture_timestamp else None,
             "latitude": p.latitude, "longitude": p.longitude, "reviewer": p.reviewer,
             "rejection_reason": p.rejection_reason} for p in proof_service.proofs_for_work_order(session, tenant, wo.id)]


@app.post("/api/workforce/qa/{work_order_id}/approve", dependencies=[Depends(management_auth)])
def qa_approve(work_order_id: UUID, payload: ReviewIn, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        review = qa_service.approve_review(session, _tid(tenant_id), work_order_id, reviewer=_actor(request),
                                           reason=payload.reason, correlation_id=payload.correlation_id)
        return {"id": str(review.id), "state": review.state, "work_order_id": str(work_order_id)}
    return _run(session, fn, request)


@app.post("/api/workforce/qa/{work_order_id}/reject", dependencies=[Depends(management_auth)])
def qa_reject(work_order_id: UUID, payload: ReviewIn, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        if not payload.reason:
            raise HTTPException(422, "rejection requires a reason")
        review = qa_service.reject_review(session, _tid(tenant_id), work_order_id, reviewer=_actor(request),
                                          reason=payload.reason, rework=payload.rework,
                                          correlation_id=payload.correlation_id)
        return {"id": str(review.id), "state": review.state, "work_order_id": str(work_order_id)}
    return _run(session, fn, request)


# ===========================================================================
# Field SLA
# ===========================================================================
@app.post("/api/workforce/sla/policies", status_code=status.HTTP_201_CREATED, dependencies=[Depends(management_auth)])
def create_sla_policy(payload: SLAPolicyCreate, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        policy = sla_service.create_policy(session, _tid(tenant_id), code=payload.code, name=payload.name, actor=_actor(request))
        return {"id": str(policy.id), "code": policy.code}
    return _run(session, fn, request)


@app.post("/api/workforce/sla/policies/{policy_id}/versions", status_code=status.HTTP_201_CREATED, dependencies=[Depends(management_auth)])
def create_sla_version(policy_id: UUID, payload: SLAPolicyVersionCreate, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        version = sla_service.create_version(session, _tid(tenant_id), policy_id, definition=payload.definition,
                                             targets=[t.model_dump() for t in payload.targets],
                                             actor=_actor(request), activate=payload.activate)
        return {"id": str(version.id), "version": version.version, "active": version.is_active}
    return _run(session, fn, request)


@app.post("/api/workforce/sla/policies/{policy_id}/activate", dependencies=[Depends(management_auth)])
def activate_sla_version(policy_id: UUID, payload: ActivateVersionIn, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        version = sla_service.activate_version(session, _tid(tenant_id), policy_id, payload.version, actor=_actor(request))
        return {"id": str(version.id), "version": version.version, "active": True}
    return _run(session, fn, request)


@app.get("/api/workforce/work-orders/{work_order_id}/sla", dependencies=[Depends(management_auth)])
def work_order_sla(work_order_id: UUID, tenant_id: UUID | None = Query(default=None), session: Session = Depends(db)):
    wo = _wo_or_404(session, _tid(tenant_id), work_order_id)
    sla = sla_service.get_field_sla(session, wo)
    return sla_service.sla_timeline(session, sla) if sla else None


@app.post("/api/workforce/work-orders/{work_order_id}/sla/exception", dependencies=[Depends(management_auth)])
def sla_exception(work_order_id: UUID, payload: SLAExceptionIn, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        wo = _wo_or_404(session, _tid(tenant_id), work_order_id)
        sla = sla_service.apply_exception(session, _tid(tenant_id), wo, arrival_deadline=payload.arrival_deadline,
                                          completion_deadline=payload.completion_deadline, reason=payload.reason,
                                          actor=_actor(request))
        wo.arrival_deadline = sla.arrival_deadline
        wo.completion_deadline = sla.completion_deadline
        return {"arrival_deadline": sla.arrival_deadline.isoformat(), "completion_deadline": sla.completion_deadline.isoformat()}
    return _run(session, fn, request)


@app.get("/api/workforce/sla/at-risk", dependencies=[Depends(management_auth)])
def sla_at_risk(tenant_id: UUID | None = Query(default=None), session: Session = Depends(db)):
    tenant_id = _tid(tenant_id)
    slas = list(session.scalars(select(FieldSLAInstance).where(
        FieldSLAInstance.tenant_id == tenant_id, FieldSLAInstance.status.in_(("AT_RISK", "BREACHED")))))
    return [{"work_order_id": str(s.work_order_id), "status": s.status,
             "arrival_deadline": s.arrival_deadline.isoformat(), "completion_deadline": s.completion_deadline.isoformat(),
             "at_risk_at": s.at_risk_at.isoformat() if s.at_risk_at else None,
             "breach_at": s.breach_at.isoformat() if s.breach_at else None} for s in slas]


# ===========================================================================
# Dispatch
# ===========================================================================
@app.get("/api/workforce/dispatch/unassigned", dependencies=[Depends(management_auth)])
def dispatch_unassigned(tenant_id: UUID | None = Query(default=None), session: Session = Depends(db)):
    return [serialize_work_order(wo) for wo in dispatch_service.unassigned_work(session, _tid(tenant_id))]


@app.get("/api/workforce/dispatch/board", dependencies=[Depends(management_auth)])
def dispatch_board(tenant_id: UUID | None = Query(default=None), date_: str | None = Query(default=None, alias="date"),
                   session: Session = Depends(db)):
    on_date = _date.fromisoformat(date_) if date_ else _date.today()
    return dispatch_service.technician_board(session, _tid(tenant_id), on_date)


@app.get("/api/workforce/dispatch/recommendations/{work_order_id}", dependencies=[Depends(management_auth)])
def dispatch_recommendations(work_order_id: UUID, tenant_id: UUID | None = Query(default=None), session: Session = Depends(db)):
    return {"recommendations": dispatch_service.recommendations(session, _tid(tenant_id), work_order_id)}


@app.post("/api/workforce/dispatch/validate-assignment", dependencies=[Depends(management_auth)])
async def validate_assignment(request: Request, tenant_id: UUID | None = Query(default=None),
                              session: Session = Depends(db)):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(422, "invalid body") from None
    work_order_id = body.get("work_order_id")
    technician_id = body.get("technician_id")
    if not work_order_id or not technician_id:
        raise HTTPException(422, "work_order_id and technician_id are required")
    return dispatch_service.validate_assignment(session, _tid(tenant_id), UUID(str(work_order_id)),
                                                UUID(str(technician_id)),
                                                window_start=body.get("window_start"),
                                                window_end=body.get("window_end"))


@app.post("/api/workforce/dispatch/bulk-preview", dependencies=[Depends(management_auth)])
async def bulk_preview(request: Request, tenant_id: UUID | None = Query(default=None), session: Session = Depends(db)):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(422, "invalid body") from None
    work_order_ids = body.get("work_order_ids", [])
    return {"previews": dispatch_service.bulk_assignment_preview(session, _tid(tenant_id),
                                                                 [UUID(str(x)) for x in work_order_ids])}


@app.get("/api/workforce/dispatch/plans/{technician_id}", dependencies=[Depends(management_auth)])
def get_plan(technician_id: UUID, tenant_id: UUID | None = Query(default=None),
             date_: str | None = Query(default=None, alias="date"), session: Session = Depends(db)):
    plan_date = date_ or _date.today().isoformat()
    plan = dispatch_service.get_dispatch_plan(session, _tid(tenant_id), technician_id, plan_date)
    return {"technician_id": str(technician_id), "plan_date": plan.plan_date, "sequence": plan.sequence,
            "version": plan.version}


@app.post("/api/workforce/dispatch/plans/{technician_id}/sequence", dependencies=[Depends(management_auth)])
def update_plan(technician_id: UUID, payload: PlanSequenceIn, tenant_id: UUID | None = Query(default=None),
                date_: str | None = Query(default=None, alias="date"), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    plan_date = date_ or _date.today().isoformat()
    plan = dispatch_service.get_dispatch_plan(session, _tid(tenant_id), technician_id, plan_date)

    def fn():
        from ..domain.dispatch import apply_sequence

        updated = apply_sequence(session, _tid(tenant_id), plan.id, payload.sequence,
                                 expected_version=payload.expected_version, edited_by=_actor(request))
        return {"version": updated.version, "sequence": updated.sequence}
    return _run(session, fn, request)


@app.get("/api/workforce/dispatch/plans/{technician_id}/route", dependencies=[Depends(management_auth)])
def build_route(technician_id: UUID, tenant_id: UUID | None = Query(default=None),
                date_: str | None = Query(default=None, alias="date"), session: Session = Depends(db)):
    plan_date = date_ or _date.today().isoformat()
    return dispatch_service.build_route(session, _tid(tenant_id), technician_id, plan_date)


# ===========================================================================
# Technician profiles (management)
# ===========================================================================
@app.post("/api/workforce/technicians", status_code=status.HTTP_201_CREATED, dependencies=[Depends(management_auth)])
def create_technician(payload: TechnicianCreate, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        technician = technician_service.create_technician(
            session, _tid(tenant_id), user_ref=payload.user_ref, name=payload.name, phone=payload.phone,
            email=payload.email, employment_type=payload.employment_type, team_code=payload.team_code,
            supervisor_ref=payload.supervisor_ref, base_lat=payload.base_lat, base_lng=payload.base_lng,
            vehicle_ref=payload.vehicle_ref, max_daily_capacity=payload.max_daily_capacity,
            supported_work_order_types=payload.supported_work_order_types,
            service_area_ids=payload.service_area_ids, actor=_actor(request))
        return _serialize_technician(technician)
    return _run(session, fn, request)


@app.get("/api/workforce/technicians", dependencies=[Depends(management_auth)])
def list_technicians(tenant_id: UUID | None = Query(default=None), active: bool | None = Query(default=None),
                     session: Session = Depends(db)):
    tenant_id = _tid(tenant_id)
    stmt = select(TechnicianProfile).where(TechnicianProfile.tenant_id == tenant_id).order_by(TechnicianProfile.name)
    if active is not None:
        stmt = stmt.where(TechnicianProfile.is_active.is_(active))
    return [_serialize_technician(t) for t in session.scalars(stmt)]


def _serialize_technician(t: TechnicianProfile) -> dict:
    return {"id": str(t.id), "user_ref": t.user_ref, "name": t.name, "phone": t.phone, "email": t.email,
            "employment_type": t.employment_type, "team_code": t.team_code, "supervisor_ref": t.supervisor_ref,
            "is_active": t.is_active, "operational_status": t.operational_status,
            "base_lat": t.base_lat, "base_lng": t.base_lng, "vehicle_ref": t.vehicle_ref,
            "max_daily_capacity": t.max_daily_capacity, "service_area_ids": t.service_area_ids}


@app.post("/api/workforce/technicians/{technician_id}/skills", dependencies=[Depends(management_auth)])
def add_skill(technician_id: UUID, payload: SkillIn, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        skill = technician_service.add_skill(session, _tid(tenant_id), technician_id, skill=payload.skill,
                                             proficiency=payload.proficiency, actor=_actor(request))
        return {"id": str(skill.id), "skill": skill.skill, "proficiency": skill.proficiency}
    return _run(session, fn, request)


@app.post("/api/workforce/technicians/{technician_id}/certifications", dependencies=[Depends(management_auth)])
def add_certification(technician_id: UUID, payload: CertificationIn, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    from datetime import date as _date

    def fn():
        expires = _date.fromisoformat(payload.expires_at) if payload.expires_at else None
        cert = technician_service.add_certification(session, _tid(tenant_id), technician_id,
                                                    certification=payload.certification, expires_at=expires,
                                                    actor=_actor(request))
        return {"id": str(cert.id), "certification": cert.certification, "expires_at": str(cert.expires_at)}
    return _run(session, fn, request)


@app.post("/api/workforce/technicians/{technician_id}/availability", dependencies=[Depends(management_auth)])
def set_availability(technician_id: UUID, payload: AvailabilityIn, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    from datetime import date as _date

    def fn():
        row = technician_service.set_availability(session, _tid(tenant_id), technician_id,
                                                  available_date=_date.fromisoformat(payload.available_date),
                                                  start_time=payload.start_time, end_time=payload.end_time,
                                                  status=payload.status, actor=_actor(request))
        return {"id": str(row.id), "available_date": str(row.available_date), "status": row.status}
    return _run(session, fn, request)


@app.post("/api/workforce/technicians/{technician_id}/shifts", dependencies=[Depends(management_auth)])
def set_shift(technician_id: UUID, payload: ShiftIn, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        row = technician_service.set_shift(session, _tid(tenant_id), technician_id, day_of_week=payload.day_of_week,
                                           start_time=payload.start_time, end_time=payload.end_time, actor=_actor(request))
        return {"id": str(row.id), "day_of_week": row.day_of_week}
    return _run(session, fn, request)


@app.post("/api/workforce/technicians/{technician_id}/status", dependencies=[Depends(management_auth)])
def set_status(technician_id: UUID, payload: StatusIn, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        tech = technician_service.transition_status(session, _tid(tenant_id), technician_id, to_status=payload.status,
                                                    source=payload.source, actor=_actor(request),
                                                    correlation_id=payload.correlation_id)
        return {"id": str(tech.id), "status": tech.operational_status}
    return _run(session, fn, request)


@app.post("/api/workforce/technicians/{technician_id}/certification-exceptions", dependencies=[Depends(management_auth)])
def certification_exception(technician_id: UUID, payload: CertExceptionIn, tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):  # noqa: E501
    def fn():
        from .domain import technicians as tech_rules

        result = tech_rules.add_certification_exception(session, _tid(tenant_id), technician_id,
                                                        payload.certification, reason=payload.reason,
                                                        approved_by=_actor(request))
        return result
    return _run(session, fn, request)


# ===========================================================================
# Technician mobile API
# ===========================================================================
def _technician(request: Request) -> dict:
    return technician_principal(request)


def _tech_wo(session: Session, request: Request, work_order_id: UUID) -> WorkOrder:
    principal = _technician(request)
    tenant = UUID(principal["tenant_id"])
    technician_id = UUID(principal["technician_id"])
    wo = _wo_or_404(session, tenant, work_order_id)
    if str(wo.assigned_technician_id) != str(technician_id):
        raise HTTPException(403, "work order not assigned to this technician")
    return wo


@app.get("/api/workforce/technician/me", dependencies=[Depends(technician_auth)])
def technician_me(request: Request):
    return _technician(request)


@app.get("/api/workforce/technician/assignments", dependencies=[Depends(technician_auth)])
def technician_assignments(request: Request, session: Session = Depends(db)):
    principal = _technician(request)
    tenant = UUID(principal["tenant_id"])
    technician_id = UUID(principal["technician_id"])
    orders = list(session.scalars(select(WorkOrder).where(
        WorkOrder.tenant_id == tenant, WorkOrder.assigned_technician_id == technician_id,
        WorkOrder.status.notin_(("COMPLETED", "FAILED", "CANCELLED"))).order_by(WorkOrder.scheduled_start)))
    return [_serialize_technician_wo(wo) for wo in orders]


def _serialize_technician_wo(wo: WorkOrder) -> dict:
    """Technician-safe view: only the customer info necessary for the work."""
    data = serialize_work_order(wo, include_internal=False)
    data["checklist"] = wo.checklist_snapshot
    data["completion_requirements"] = wo.completion_requirements
    return data


@app.get("/api/workforce/technician/assignments/{work_order_id}", dependencies=[Depends(technician_auth)])
def technician_assignment_detail(work_order_id: UUID, request: Request, session: Session = Depends(db)):
    wo = _tech_wo(session, request, work_order_id)
    return _serialize_technician_wo(wo)


@app.post("/api/workforce/technician/assignments/{work_order_id}/accept", dependencies=[Depends(technician_auth)])
def technician_accept(work_order_id: UUID, request: Request, session: Session = Depends(db)):
    principal = _technician(request)
    tenant = UUID(principal["tenant_id"])
    technician_id = UUID(principal["technician_id"])
    wo = _tech_wo(session, request, work_order_id)

    def fn():
        return serialize_work_order(workorder_service.accept_assignment(session, tenant, work_order_id,
                                                                        technician_id=technician_id,
                                                                        actor=principal["subject"]))
    return _run(session, fn, request)


@app.post("/api/workforce/technician/assignments/{work_order_id}/reject", dependencies=[Depends(technician_auth)])
def technician_reject(work_order_id: UUID, payload: ReasonIn, request: Request, session: Session = Depends(db)):
    principal = _technician(request)
    tenant = UUID(principal["tenant_id"])
    technician_id = UUID(principal["technician_id"])
    _tech_wo(session, request, work_order_id)

    def fn():
        return serialize_work_order(workorder_service.reject_assignment(session, tenant, work_order_id,
                                                                        technician_id=technician_id,
                                                                        reason=payload.reason, actor=principal["subject"]))
    return _run(session, fn, request)


def _tech_run(session, fn):
    try:
        result = fn()
        session.commit()
        return result
    except WorkforceError as error:
        session.rollback()
        _raise(error)


@app.post("/api/workforce/technician/assignments/{work_order_id}/start-travel", dependencies=[Depends(technician_auth)])
def technician_start_travel(work_order_id: UUID, request: Request, session: Session = Depends(db)):
    principal = _technician(request)
    tenant = UUID(principal["tenant_id"])
    _tech_wo(session, request, work_order_id)
    return _tech_run(session, lambda: serialize_work_order(
        workorder_service.start_travel(session, tenant, work_order_id, actor=principal["subject"])))


@app.post("/api/workforce/technician/assignments/{work_order_id}/check-in", dependencies=[Depends(technician_auth)])
def technician_check_in(work_order_id: UUID, payload: CheckInIn, request: Request, session: Session = Depends(db)):
    principal = _technician(request)
    tenant = UUID(principal["tenant_id"])
    technician_id = UUID(principal["technician_id"])
    _tech_wo(session, request, work_order_id)

    def fn():
        wo = workorder_service.check_in_work_order(
            session, tenant, work_order_id, technician_id=technician_id,
            payload=payload.model_dump(), actor=principal["subject"],
            correlation_id=payload.correlation_id, device_ref=principal.get("device_ref"))
        return {"work_order": serialize_work_order(wo), "status": "ARRIVED"}
    return _tech_run(session, fn)


@app.post("/api/workforce/technician/assignments/{work_order_id}/check-out", dependencies=[Depends(technician_auth)])
def technician_check_out(work_order_id: UUID, payload: CheckInIn, request: Request, session: Session = Depends(db)):
    principal = _technician(request)
    tenant = UUID(principal["tenant_id"])
    technician_id = UUID(principal["technician_id"])
    _tech_wo(session, request, work_order_id)

    def fn():
        workorder_service.check_out_work_order(session, tenant, work_order_id, technician_id=technician_id,
                                               payload=payload.model_dump(), actor=principal["subject"],
                                               correlation_id=payload.correlation_id, device_ref=principal.get("device_ref"))
        return {"status": "CHECKED_OUT"}
    return _tech_run(session, fn)


@app.post("/api/workforce/technician/assignments/{work_order_id}/start-work", dependencies=[Depends(technician_auth)])
def technician_start_work(work_order_id: UUID, request: Request, session: Session = Depends(db)):
    principal = _technician(request)
    tenant = UUID(principal["tenant_id"])
    _tech_wo(session, request, work_order_id)
    return _tech_run(session, lambda: serialize_work_order(
        workorder_service.start_work(session, tenant, work_order_id, actor=principal["subject"])))


@app.post("/api/workforce/technician/assignments/{work_order_id}/pause", dependencies=[Depends(technician_auth)])
def technician_pause(work_order_id: UUID, payload: ReasonIn, request: Request, session: Session = Depends(db)):
    principal = _technician(request)
    tenant = UUID(principal["tenant_id"])
    _tech_wo(session, request, work_order_id)
    return _tech_run(session, lambda: serialize_work_order(
        workorder_service.pause_work_order(session, tenant, work_order_id, reason=payload.reason,
                                           actor=principal["subject"])))


@app.post("/api/workforce/technician/assignments/{work_order_id}/resume", dependencies=[Depends(technician_auth)])
def technician_resume(work_order_id: UUID, request: Request, session: Session = Depends(db)):
    principal = _technician(request)
    tenant = UUID(principal["tenant_id"])
    _tech_wo(session, request, work_order_id)
    return _tech_run(session, lambda: serialize_work_order(
        workorder_service.resume_work_order(session, tenant, work_order_id, actor=principal["subject"])))


@app.post("/api/workforce/technician/assignments/{work_order_id}/blocker", dependencies=[Depends(technician_auth)])
def technician_blocker(work_order_id: UUID, payload: BlockIn, request: Request, session: Session = Depends(db)):
    principal = _technician(request)
    tenant = UUID(principal["tenant_id"])
    _tech_wo(session, request, work_order_id)
    return _tech_run(session, lambda: serialize_work_order(
        workorder_service.record_blocker(session, tenant, work_order_id, blocker_type=payload.blocker_type,
                                         reason=payload.reason, severity=payload.severity,
                                         actor=principal["subject"], correlation_id=payload.correlation_id)))


@app.post("/api/workforce/technician/assignments/{work_order_id}/parts", dependencies=[Depends(technician_auth)])
def technician_parts(work_order_id: UUID, payload: PartsIn, request: Request, session: Session = Depends(db)):
    principal = _technician(request)
    tenant = UUID(principal["tenant_id"])
    _tech_wo(session, request, work_order_id)
    return _tech_run(session, lambda: serialize_work_order(
        workorder_service.request_parts(session, tenant, work_order_id, materials=payload.materials,
                                        reason=payload.reason, actor=principal["subject"],
                                        correlation_id=payload.correlation_id)))


@app.post("/api/workforce/technician/assignments/{work_order_id}/remote-action", dependencies=[Depends(technician_auth)])
def technician_remote_action(work_order_id: UUID, request: Request, session: Session = Depends(db)):
    principal = _technician(request)
    tenant = UUID(principal["tenant_id"])
    _tech_wo(session, request, work_order_id)
    return _tech_run(session, lambda: serialize_work_order(
        workorder_service.request_remote_action(session, tenant, work_order_id, actor=principal["subject"])))


@app.post("/api/workforce/technician/assignments/{work_order_id}/checklist", dependencies=[Depends(technician_auth)])
def technician_checklist_submit(work_order_id: UUID, payload: ChecklistSubmitIn, request: Request, session: Session = Depends(db)):  # noqa: E501
    principal = _technician(request)
    tenant = UUID(principal["tenant_id"])
    _tech_wo(session, request, work_order_id)

    def fn():
        checklist = checklist_service.submit_responses(session, tenant, work_order_id,
                                                       responses=payload.responses,
                                                       submitted_by=principal["subject"],
                                                       correlation_id=payload.correlation_id)
        return {"checklist_id": str(checklist.id), "responses": len(payload.responses)}
    return _tech_run(session, fn)


@app.post("/api/workforce/technician/assignments/{work_order_id}/proof", dependencies=[Depends(technician_auth)])
def technician_proof(work_order_id: UUID, payload: ProofIn, request: Request, session: Session = Depends(db)):
    principal = _technician(request)
    tenant = UUID(principal["tenant_id"])
    technician_id = UUID(principal["technician_id"])
    _tech_wo(session, request, work_order_id)

    def fn():
        proof = proof_service.add_proof(session, tenant, work_order_id, evidence_key=payload.evidence_key,
                                        evidence_type=payload.evidence_type, file_ref=payload.file_ref,
                                        checksum=payload.checksum, capture_timestamp=payload.capture_timestamp,
                                        latitude=payload.latitude, longitude=payload.longitude,
                                        device_ref=payload.device_ref, technician_id=technician_id,
                                        checklist_item_code=payload.checklist_item_code, actor=principal["subject"],
                                        correlation_id=payload.correlation_id)
        return {"proof_id": str(proof.id), "evidence_type": proof.evidence_type, "status": "RECORDED"}
    return _tech_run(session, fn)


@app.post("/api/workforce/technician/assignments/{work_order_id}/materials", dependencies=[Depends(technician_auth)])
def technician_materials(work_order_id: UUID, payload: MaterialIn, request: Request, session: Session = Depends(db)):
    principal = _technician(request)
    tenant = UUID(principal["tenant_id"])
    technician_id = UUID(principal["technician_id"])
    _tech_wo(session, request, work_order_id)

    def fn():
        usage = inventory_service.record_material_usage(session, tenant, work_order_id,
                                                        material_code=payload.material_code,
                                                        quantity=payload.quantity, usage_type=payload.usage_type,
                                                        technician_id=technician_id, actor=principal["subject"],
                                                        correlation_id=payload.correlation_id)
        return {"material_usage_id": str(usage.id), "material_code": usage.material_code}
    return _tech_run(session, fn)


@app.post("/api/workforce/technician/assignments/{work_order_id}/devices", dependencies=[Depends(technician_auth)])
def technician_devices(work_order_id: UUID, payload: DeviceIn, request: Request, session: Session = Depends(db)):
    principal = _technician(request)
    tenant = UUID(principal["tenant_id"])
    technician_id = UUID(principal["technician_id"])
    _tech_wo(session, request, work_order_id)

    def fn():
        installation = inventory_service.record_device_installation(
            session, tenant, work_order_id, device_type=payload.device_type, serial_number=payload.serial_number,
            mac_address=payload.mac_address, service_subscription_id=payload.service_subscription_id,
            technician_id=technician_id, actor=principal["subject"], correlation_id=payload.correlation_id)
        return {"device_installation_id": str(installation.id), "serial_number": installation.serial_number}
    return _tech_run(session, fn)


@app.post("/api/workforce/technician/assignments/{work_order_id}/acknowledgement", dependencies=[Depends(technician_auth)])
def technician_acknowledgement(work_order_id: UUID, payload: AcknowledgementIn, request: Request, session: Session = Depends(db)):  # noqa: E501
    principal = _technician(request)
    tenant = UUID(principal["tenant_id"])
    _tech_wo(session, request, work_order_id)

    def fn():
        ack = proof_service.record_customer_acknowledgement(
            session, tenant, work_order_id, method=payload.method, masked_recipient=payload.masked_recipient,
            consent_text_version=payload.consent_text_version, result=payload.result, exception=payload.exception,
            actor=principal["subject"])
        return {"acknowledgement_id": str(ack.id), "method": ack.method}
    return _tech_run(session, fn)


@app.post("/api/workforce/technician/assignments/{work_order_id}/finish", dependencies=[Depends(technician_auth)])
def technician_finish(work_order_id: UUID, request: Request, session: Session = Depends(db)):
    principal = _technician(request)
    tenant = UUID(principal["tenant_id"])
    _tech_wo(session, request, work_order_id)
    return _tech_run(session, lambda: serialize_work_order(
        workorder_service.finish_execution(session, tenant, work_order_id, actor=principal["subject"])))


@app.post("/api/workforce/technician/assignments/{work_order_id}/verify", dependencies=[Depends(technician_auth)])
def technician_verify(work_order_id: UUID, request: Request, session: Session = Depends(db)):
    principal = _technician(request)
    tenant = UUID(principal["tenant_id"])
    _tech_wo(session, request, work_order_id)
    return _tech_run(session, lambda: serialize_work_order(
        workorder_service.submit_for_verification(session, tenant, work_order_id, actor=principal["subject"])))


@app.post("/api/workforce/technician/assignments/{work_order_id}/attachments", dependencies=[Depends(technician_auth)])
async def technician_attachment(work_order_id: UUID, request: Request, file: UploadFile = File(...),
                                session: Session = Depends(db)):
    principal = _technician(request)
    tenant = UUID(principal["tenant_id"])
    _tech_wo(session, request, work_order_id)
    try:
        attachment = await proof_service.store_attachment(session, tenant, work_order_id, file,
                                                          uploader_type="TECHNICIAN",
                                                          uploader_id=principal["subject"])
        session.commit()
        return {"id": str(attachment.id), "original_name": attachment.original_name, "size_bytes": attachment.size_bytes,
                "checksum_sha256": attachment.checksum_sha256}
    except WorkforceError as error:
        session.rollback()
        _raise(error)


@app.get("/api/workforce/work-orders/{work_order_id}/attachments/{attachment_id}/download", dependencies=[Depends(management_auth)])
def download_attachment(work_order_id: UUID, attachment_id: UUID, tenant_id: UUID | None = Query(default=None),
                        session: Session = Depends(db)):
    from fastapi.responses import FileResponse

    try:
        path, content_type = proof_service.load_attachment(session, _tid(tenant_id), work_order_id, attachment_id)
        return FileResponse(path, media_type=content_type)
    except WorkforceError as error:
        _raise(error)


@app.post("/api/workforce/technician/sync", dependencies=[Depends(technician_auth)])
def technician_sync(payload: OfflineSyncIn, request: Request, session: Session = Depends(db)):
    principal = _technician(request)
    tenant = UUID(principal["tenant_id"])
    return offline_service.process_offline_commands(session, tenant, device_ref=payload.device_ref,
                                                    commands=payload.commands, actor=principal["subject"])


# ===========================================================================
# Customer portal
# ===========================================================================
@app.get("/api/workforce/portal/appointments", dependencies=[Depends(customer_auth)])
def portal_appointments(request: Request, session: Session = Depends(db)):
    principal = customer_principal(request)
    tenant = UUID(principal["tenant_id"])
    customer_id = principal["customer_id"]
    rows = list(session.scalars(select(Appointment).join(WorkOrder, Appointment.work_order_id == WorkOrder.id).where(
        WorkOrder.tenant_id == tenant, WorkOrder.customer_id == customer_id,
        Appointment.status.in_(("CUSTOMER_CONFIRMATION_PENDING", "CONFIRMED", "TECHNICIAN_DISPATCHED")))))
    return [{"id": str(a.id), "work_order_number": _wo_number(session, a.work_order_id),
             "window_start": a.window_start.isoformat(), "window_end": a.window_end.isoformat(),
             "status": a.status, "attempt_number": a.attempt_number} for a in rows]


def _wo_number(session: Session, work_order_id) -> str | None:
    wo = session.get(WorkOrder, work_order_id)
    return wo.work_order_number if wo else None


@app.post("/api/workforce/portal/appointments/{appointment_id}/confirm", dependencies=[Depends(customer_auth)])
def portal_confirm_appointment(appointment_id: UUID, request: Request, session: Session = Depends(db)):
    principal = customer_principal(request)
    tenant = UUID(principal["tenant_id"])
    appointment = appointment_service.get_appointment_or_404(session, tenant, appointment_id)
    _assert_customer_appointment(session, tenant, principal["customer_id"], appointment_id)

    def fn():
        return serialize_appointment(appointment_service.confirm(session, tenant, appointment_id, actor=principal["subject"]))
    return _run(session, fn, request)


@app.post("/api/workforce/portal/appointments/{appointment_id}/reschedule", dependencies=[Depends(customer_auth)])
def portal_reschedule(appointment_id: UUID, payload: ScheduleIn, request: Request, session: Session = Depends(db)):
    principal = customer_principal(request)
    tenant = UUID(principal["tenant_id"])
    _assert_customer_appointment(session, tenant, principal["customer_id"], appointment_id)

    def fn():
        appointment = appointment_service.reschedule(session, tenant, appointment_id,
                                                     window_start=payload.window_start, window_end=payload.window_end,
                                                     reason="customer requested reschedule", actor=principal["subject"])
        return serialize_appointment(appointment)
    return _run(session, fn, request)


def _assert_customer_appointment(session: Session, tenant, customer_id: str, appointment_id: UUID) -> None:
    appointment = appointment_service.get_appointment_or_404(session, tenant, appointment_id)
    wo = session.get(WorkOrder, appointment.work_order_id)
    if wo is None or wo.customer_id != customer_id:
        raise HTTPException(403, "appointment not for this customer")


@app.get("/api/workforce/portal/work-orders/{work_order_id}", dependencies=[Depends(customer_auth)])
def portal_work_order(work_order_id: UUID, request: Request, session: Session = Depends(db)):
    """Privacy-safe status view for the customer: no exact technician location,
    no internal notes, no proof files."""
    principal = customer_principal(request)
    tenant = UUID(principal["tenant_id"])
    wo = _wo_or_404(session, tenant, work_order_id)
    if wo.customer_id != principal["customer_id"]:
        raise HTTPException(403, "work order not for this customer")
    appointment = session.get(Appointment, wo.current_appointment_id) if wo.current_appointment_id else None
    return {
        "work_order_number": wo.work_order_number,
        "work_order_type": wo.work_order_type,
        "status": wo.status,
        "scheduled_start": wo.scheduled_start.isoformat() if wo.scheduled_start else None,
        "scheduled_end": wo.scheduled_end.isoformat() if wo.scheduled_end else None,
        "expected_arrival_deadline": wo.arrival_deadline.isoformat() if wo.arrival_deadline else None,
        "appointment_status": appointment.status if appointment else None,
        "technician_status": "privacy-safe" if wo.assigned_technician_id else None,
        "result_code": wo.result_code,
    }


# ===========================================================================
# Reports / audit
# ===========================================================================
@app.get("/api/workforce/reports/overview", dependencies=[Depends(management_auth)])
def report_overview(tenant_id: UUID | None = Query(default=None), session: Session = Depends(db)):
    tenant_id = _tid(tenant_id)
    from .enums import OPEN_WORK_ORDER_STATES

    open_states = tuple(OPEN_WORK_ORDER_STATES)
    return {
        "open_work_orders": session.scalar(select(func.count(WorkOrder.id)).where(
            WorkOrder.tenant_id == tenant_id, WorkOrder.status.in_(open_states))) or 0,
        "unassigned": session.scalar(select(func.count(WorkOrder.id)).where(
            WorkOrder.tenant_id == tenant_id, WorkOrder.assigned_technician_id.is_(None),
            WorkOrder.status.in_(open_states))) or 0,
        "at_risk": session.scalar(select(func.count(FieldSLAInstance.id)).where(
            FieldSLAInstance.tenant_id == tenant_id, FieldSLAInstance.status == "AT_RISK")) or 0,
        "breached": session.scalar(select(func.count(FieldSLAInstance.id)).where(
            FieldSLAInstance.tenant_id == tenant_id, FieldSLAInstance.status == "BREACHED")) or 0,
        "qa_pending": session.scalar(select(func.count(models.QualityReview.id)).where(
            models.QualityReview.tenant_id == tenant_id, models.QualityReview.state.in_(("PENDING", "UNDER_REVIEW")))) or 0,
        "completed": session.scalar(select(func.count(WorkOrder.id)).where(
            WorkOrder.tenant_id == tenant_id, WorkOrder.status == "COMPLETED")) or 0,
    }


@app.get("/api/workforce/reports/tickets", dependencies=[Depends(management_auth)])
def report_by_status(tenant_id: UUID | None = Query(default=None), session: Session = Depends(db)):
    tenant_id = _tid(tenant_id)
    rows = session.execute(select(WorkOrder.status, func.count(WorkOrder.id)).where(
        WorkOrder.tenant_id == tenant_id).group_by(WorkOrder.status)).all()
    return {"by_status": {status_: count for status_, count in rows}}


@app.get("/api/workforce/audit", dependencies=[Depends(management_auth)])
def audit_log(tenant_id: UUID | None = Query(default=None), session: Session = Depends(db)):
    tenant_id = _tid(tenant_id)
    from .models import AuditLog

    rows = list(session.scalars(select(AuditLog).where(AuditLog.tenant_id == tenant_id)
                                .order_by(AuditLog.created_at.desc()).limit(200)))
    return [{"id": str(a.id), "event_type": a.event_type, "entity_type": a.entity_type, "entity_id": a.entity_id,
             "actor": a.actor, "reason": a.reason,
             "created_at": a.created_at.isoformat() if a.created_at else None} for a in rows]

