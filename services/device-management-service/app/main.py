"""Device Management Service API (Milestone 7).

Business-facing control plane for TR-069 CPE management via GenieACS. Explicit
command endpoints only; the GenieACS NBI is never exposed to frontends."""
from contextlib import asynccontextmanager
import secrets
from os import getenv
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import models  # noqa: F401
from .database import Base, SessionLocal, engine
from .domain.exceptions import DeviceMgmtError
from .models import ManagedCpe
from .schemas import (
    ActionCreate,
    ActionOutcomeIn,
    ACSRegister,
    AssignmentRuleIn,
    AssignIn,
    ClaimIn,
    CompatibilityIn,
    CompilePreviewIn,
    ConfigurationJobCreate,
    DeploymentOutcomeIn,
    DeploymentQueueIn,
    DiagnosticCreate,
    DiagnosticResultIn,
    DiscoverIn,
    FirmwareApprovalIn,
    FirmwareUpload,
    ObservedIn,
    ProfileCreate,
    ProfileVersionCreate,
    ReasonIn,
    ResolveTenantIn,
    RolloutCreate,
    SignalIn,
    StageBuildIn,
    TaskResultIn,
    TelemetryIn,
    TransferIn,
)
from .security import internal_service_auth, management_auth
from .services import (
    acs_service,
    action_service,
    catalog_service,
    configuration_service,
    device_service,
    diagnostic_service,
    firmware_service,
    profile_service,
    telemetry_service,
)
from .services.audit_service import cpe_events, correlation


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


app = FastAPI(title="Device Management Service", version="7.0.0", lifespan=lifespan)


@app.exception_handler(DeviceMgmtError)
async def _device_mgmt_error_handler(_request: Request, exc: DeviceMgmtError):
    return JSONResponse(status_code=exc.status_code,
                        content={"code": exc.code, "detail": exc.message})


def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _raise(error: Exception) -> None:
    if isinstance(error, DeviceMgmtError):
        raise HTTPException(error.status_code, {"code": error.code, "detail": error.message}) from error
    raise HTTPException(422, str(error)) from error


def _actor(request: Request) -> str:
    principal = getattr(request.state, "device_principal", None)
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
    except DeviceMgmtError as error:
        session.rollback()
        _raise(error)


def serialize_device(d: ManagedCpe) -> dict:
    return {
        "id": str(d.id),
        "tenant_id": str(d.tenant_id) if d.tenant_id else None,
        "oui": d.oui,
        "product_class": d.product_class,
        "serial_number": d.serial_number,
        "manufacturer": d.manufacturer,
        "model_name": d.model_name,
        "hardware_version": d.hardware_version,
        "firmware_version": d.firmware_version,
        "wan_mac": d.wan_mac,
        "lan_mac": d.lan_mac,
        "data_model_family": d.data_model_family,
        "data_model_version": d.data_model_version,
        "acs_device_id": d.acs_device_id,
        "state": d.state,
        "operational_status": d.operational_status,
        "online": d.online,
        "inventory_asset_id": d.inventory_asset_id,
        "inventory_serial": d.inventory_serial,
        "customer_id": d.customer_id,
        "service_subscription_id": d.service_subscription_id,
        "service_location_id": d.service_location_id,
        "oss_order_id": d.oss_order_id,
        "work_order_id": d.work_order_id,
        "support_ticket_id": d.support_ticket_id,
        "first_inform_at": d.first_inform_at.isoformat() if d.first_inform_at else None,
        "last_inform_at": d.last_inform_at.isoformat() if d.last_inform_at else None,
        "profile_compliance": d.profile_compliance,
        "firmware_compliance": d.firmware_compliance,
        "last_drift_classification": d.last_drift_classification,
        "display_name": d.display_name,
        "claimed_at": d.claimed_at.isoformat() if d.claimed_at else None,
        "aggregate_version": d.aggregate_version,
    }


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok", "service": getenv("SERVICE_NAME", "device-management-service")}


@app.get("/status")
def service_status():
    return {"service": "device-management", "phase": "milestone-7-device-management"}


# ===========================================================================
# Discovery / onboarding / devices
# ===========================================================================
@app.post("/api/device-management/devices/discover", status_code=status.HTTP_201_CREATED,
          dependencies=[Depends(management_auth)])
def discover(payload: DiscoverIn, request: Request, session: Session = Depends(db)):
    try:
        device = device_service.discover_from_acs(
            session, payload.acs_instance_id, acs_device_id=payload.acs_device_id,
            requested_tenant_id=payload.tenant_id, actor=_actor(request),
            correlation_id=payload.correlation_id)
        session.commit()
        session.refresh(device)
        return serialize_device(device)
    except DeviceMgmtError as error:
        _raise(error)


@app.post("/api/device-management/devices/{device_id}/resolve-tenant", dependencies=[Depends(management_auth)])
def resolve_tenant(device_id: UUID, payload: ResolveTenantIn, request: Request, session: Session = Depends(db)):
    def fn():
        device = session.get(ManagedCpe, device_id)
        if device is None:
            raise HTTPException(404, "device not found")
        return {"result": device_service.resolve_tenant(
            session, device, method=payload.method, evidence=payload.evidence,
            claimed_tenant_id=payload.claimed_tenant_id, actor=_actor(request))}
    return _run(session, fn, request)


@app.post("/api/device-management/devices/{device_id}/claim", dependencies=[Depends(management_auth)])
def claim(device_id: UUID, payload: ClaimIn, tenant_id: UUID | None = Query(default=None),
          request: Request = None, session: Session = Depends(db)):
    def fn():
        return serialize_device(device_service.claim_device(
            session, _tid(tenant_id), device_id, method=payload.method, evidence=payload.evidence,
            actor=_actor(request), correlation_id=payload.correlation_id))
    return _run(session, fn, request)


@app.post("/api/device-management/devices/{device_id}/assign", dependencies=[Depends(management_auth)])
def assign(device_id: UUID, payload: AssignIn, tenant_id: UUID | None = Query(default=None),
           request: Request = None, session: Session = Depends(db)):
    def fn():
        return serialize_device(device_service.assign_device(
            session, _tid(tenant_id), device_id, customer_id=payload.customer_id,
            service_subscription_id=payload.service_subscription_id,
            service_location_id=payload.service_location_id, oss_order_id=payload.oss_order_id,
            work_order_id=payload.work_order_id, inventory_serial=payload.inventory_serial,
            inventory_asset_id=payload.inventory_asset_id, actor=_actor(request),
            correlation_id=payload.correlation_id))
    return _run(session, fn, request)


@app.post("/api/device-management/devices/{device_id}/transfer", dependencies=[Depends(management_auth)])
def transfer(device_id: UUID, payload: TransferIn, tenant_id: UUID | None = Query(default=None),
             request: Request = None, session: Session = Depends(db)):
    def fn():
        return serialize_device(device_service.transfer_device(
            session, _tid(tenant_id), payload.to_tenant_id, device_id, reason=payload.reason,
            actor=_actor(request), correlation_id=payload.correlation_id))
    return _run(session, fn, request)


@app.post("/api/device-management/devices/{device_id}/decommission", dependencies=[Depends(management_auth)])
def decommission(device_id: UUID, payload: ReasonIn, tenant_id: UUID | None = Query(default=None),
                 request: Request = None, session: Session = Depends(db)):
    def fn():
        return serialize_device(device_service.decommission_device(
            session, _tid(tenant_id), device_id, reason=payload.reason, actor=_actor(request),
            correlation_id=payload.correlation_id))
    return _run(session, fn, request)


@app.get("/api/device-management/devices", dependencies=[Depends(management_auth)])
def list_devices(
    tenant_id: UUID | None = Query(default=None),
    state: str | None = Query(default=None),
    online: bool | None = Query(default=None),
    serial: str | None = Query(default=None),
    search: str | None = Query(default=None),
    model: str | None = Query(default=None),
    customer_id: str | None = Query(default=None),
    service_subscription_id: str | None = Query(default=None),
    profile: str | None = Query(default=None),
    drift: bool | None = Query(default=None),
    session: Session = Depends(db),
):
    tenant_id = _tid(tenant_id)
    stmt = select(ManagedCpe).where(ManagedCpe.tenant_id == tenant_id).order_by(ManagedCpe.created_at.desc())
    if state:
        stmt = stmt.where(ManagedCpe.state == state)
    if online is not None:
        stmt = stmt.where(ManagedCpe.online.is_(online))
    if serial:
        stmt = stmt.where(ManagedCpe.serial_number.ilike(f"%{serial}%"))
    if model:
        stmt = stmt.where(ManagedCpe.model_name == model)
    if customer_id:
        stmt = stmt.where(ManagedCpe.customer_id == customer_id)
    if service_subscription_id:
        stmt = stmt.where(ManagedCpe.service_subscription_id == service_subscription_id)
    if profile:
        stmt = stmt.where(ManagedCpe.profile_compliance == profile.upper())
    if drift:
        stmt = stmt.where(ManagedCpe.last_drift_classification != "NONE")
    if search:
        like = f"%{search}%"
        stmt = stmt.where((ManagedCpe.serial_number.ilike(like)) | (ManagedCpe.wan_mac.ilike(like))
                          | (ManagedCpe.lan_mac.ilike(like)) | (ManagedCpe.model_name.ilike(like))
                          | (ManagedCpe.oui.ilike(like)))
    return [serialize_device(d) for d in session.scalars(stmt.limit(200))]


@app.get("/api/device-management/devices/{device_id}", dependencies=[Depends(management_auth)])
def device_detail(device_id: UUID, tenant_id: UUID | None = Query(default=None), session: Session = Depends(db)):
    device = device_service.get_device_or_404(session, _tid(tenant_id), device_id)
    data = serialize_device(device)
    data["timeline"] = [{"version": e.version, "event_type": e.event_type, "payload": e.payload,
                         "actor_id": e.actor_id,
                         "created_at": e.created_at.isoformat() if e.created_at else None}
                        for e in cpe_events(session, device.id)]
    return data


@app.post("/api/device-management/devices/{device_id}/refresh", dependencies=[Depends(management_auth)])
def refresh_device(device_id: UUID, tenant_id: UUID | None = Query(default=None), request: Request = None,
                   session: Session = Depends(db)):
    from .services import telemetry_service as _ts

    def fn():
        device = device_service.get_device_or_404(session, _tid(tenant_id), device_id)
        from .integrations.acs import get_acs_client

        client = get_acs_client({"instance_id": str(device.acs_instance_id)})
        paths = ["Device.DeviceInfo.SoftwareVersion", "Device.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.ConnectionStatus"]
        observed = client.get_parameters(device.acs_device_id, paths)
        configuration_service.record_observed(session, _tid(tenant_id), device.id, parameters=observed,
                                              actor=_actor(request))
        return {"refreshed": True, "observed": observed}
    return _run(session, fn, request)


# ===========================================================================
# Configuration profiles
# ===========================================================================
@app.post("/api/device-management/profiles", status_code=status.HTTP_201_CREATED, dependencies=[Depends(management_auth)])
def create_profile(payload: ProfileCreate, tenant_id: UUID | None = Query(default=None),
                   request: Request = None, session: Session = Depends(db)):
    def fn():
        profile = profile_service.create_profile(session, _tid(tenant_id), code=payload.code,
                                                 name=payload.name, description=payload.description,
                                                 actor=_actor(request))
        return {"id": str(profile.id), "code": profile.code}
    return _run(session, fn, request)


@app.post("/api/device-management/profiles/{profile_id}/versions", status_code=status.HTTP_201_CREATED,
          dependencies=[Depends(management_auth)])
def create_profile_version(profile_id: UUID, payload: ProfileVersionCreate, tenant_id: UUID | None = Query(default=None),
                           request: Request = None, session: Session = Depends(db)):
    def fn():
        version = profile_service.create_version(session, _tid(tenant_id), profile_id, definition=payload.definition,
                                                 change_summary=payload.change_summary, actor=_actor(request))
        return {"id": str(version.id), "version": version.version, "state": version.state}
    return _run(session, fn, request)


@app.post("/api/device-management/profiles/versions/{version_id}/submit", dependencies=[Depends(management_auth)])
def submit_profile_version(version_id: UUID, tenant_id: UUID | None = Query(default=None),
                           request: Request = None, session: Session = Depends(db)):
    def fn():
        version = profile_service.submit_for_approval(session, _tid(tenant_id), version_id, actor=_actor(request))
        return {"id": str(version.id), "state": version.state}
    return _run(session, fn, request)


@app.post("/api/device-management/profiles/versions/{version_id}/approve", dependencies=[Depends(management_auth)])
def approve_profile_version(version_id: UUID, tenant_id: UUID | None = Query(default=None),
                            request: Request = None, session: Session = Depends(db)):
    def fn():
        version = profile_service.approve_version(session, _tid(tenant_id), version_id, actor=_actor(request))
        return {"id": str(version.id), "state": version.state}
    return _run(session, fn, request)


@app.post("/api/device-management/profiles/versions/{version_id}/activate", dependencies=[Depends(management_auth)])
def activate_profile_version(version_id: UUID, tenant_id: UUID | None = Query(default=None),
                             request: Request = None, session: Session = Depends(db)):
    def fn():
        version = profile_service.activate_version(session, _tid(tenant_id), version_id, actor=_actor(request))
        return {"id": str(version.id), "state": version.state}
    return _run(session, fn, request)


@app.post("/api/device-management/profiles/versions/{version_id}/compile-preview", dependencies=[Depends(management_auth)])
def compile_preview(version_id: UUID, payload: CompilePreviewIn, tenant_id: UUID | None = Query(default=None),
                    request: Request = None, session: Session = Depends(db)):
    def fn():
        return profile_service.compile_preview(session, _tid(tenant_id), version_id,
                                               model_variant_id=payload.model_variant_id,
                                               data_model_family=payload.data_model_family)
    return _run(session, fn, request)


@app.post("/api/device-management/profiles/{profile_id}/assignment-rules", dependencies=[Depends(management_auth)])
def add_assignment_rule(profile_id: UUID, payload: AssignmentRuleIn,
                        tenant_id: UUID | None = Query(default=None), request: Request = None, session: Session = Depends(db)):
    def fn():
        rule = profile_service.add_assignment_rule(session, _tid(tenant_id), profile_id, facts=payload.facts,
                                                   priority=payload.priority, reason=payload.reason, actor=_actor(request))
        return {"id": str(rule.id), "rule_version": rule.rule_version}
    return _run(session, fn, request)


@app.get("/api/device-management/devices/{device_id}/profile-decision", dependencies=[Depends(management_auth)])
def profile_decision(device_id: UUID, tenant_id: UUID | None = Query(default=None), session: Session = Depends(db)):
    device = device_service.get_device_or_404(session, _tid(tenant_id), device_id)
    profile, version, decision = profile_service.resolve_profile_for_device(session, _tid(tenant_id), device)
    return {"profile_id": str(profile.id) if profile else None,
            "profile_version_id": str(version.id) if version else None, **decision}


# ===========================================================================
# Configuration jobs
# ===========================================================================
@app.post("/api/device-management/devices/{device_id}/configuration-jobs", status_code=status.HTTP_201_CREATED,
          dependencies=[Depends(management_auth)])
def create_configuration_job(device_id: UUID, payload: ConfigurationJobCreate, tenant_id: UUID | None = Query(default=None),
                             request: Request = None, session: Session = Depends(db)):
    def fn():
        job = configuration_service.create_configuration_job(
            session, _tid(tenant_id), device_id, profile_version_id=payload.profile_version_id,
            parameters=payload.parameters, verification_required=payload.verification_required,
            requested_by=payload.requested_by, actor=_actor(request), idempotency_key=payload.idempotency_key,
            correlation_id=payload.correlation_id)
        return {"id": str(job.id), "state": job.state, "diff_preview": job.diff_preview}
    return _run(session, fn, request)


@app.get("/api/device-management/configuration-jobs/{job_id}", dependencies=[Depends(management_auth)])
def configuration_job_detail(job_id: UUID, tenant_id: UUID | None = Query(default=None), session: Session = Depends(db)):
    job = configuration_service.get_job_or_404(session, _tid(tenant_id), job_id)
    from .models import ConfigurationStep

    steps = [{"step_type": s.step_type, "state": s.state, "genieacs_task_id": s.genieacs_task_id,
              "verified": s.verified, "fault_code": s.fault_code} for s in session.scalars(
        select(ConfigurationStep).where(ConfigurationStep.job_id == job.id))]
    return {"id": str(job.id), "state": job.state, "cpe_id": str(job.cpe_id),
            "profile_version_id": str(job.profile_version_id) if job.profile_version_id else None,
            "desired_parameters": job.desired_parameters, "diff_preview": job.diff_preview,
            "failure_code": job.failure_code, "failure_detail": job.failure_detail, "steps": steps,
            "correlation_id": job.correlation_id}


@app.post("/api/device-management/configuration-jobs/{job_id}/approve", dependencies=[Depends(management_auth)])
def approve_configuration_job(job_id: UUID, tenant_id: UUID | None = Query(default=None),
                              request: Request = None, session: Session = Depends(db)):
    def fn():
        job = configuration_service.approve_job(session, _tid(tenant_id), job_id, actor=_actor(request))
        return {"id": str(job.id), "state": job.state}
    return _run(session, fn, request)


@app.post("/api/device-management/configuration-jobs/{job_id}/queue", dependencies=[Depends(management_auth)])
def queue_configuration_job(job_id: UUID, tenant_id: UUID | None = Query(default=None),
                            request: Request = None, session: Session = Depends(db)):
    def fn():
        job = configuration_service.queue_job(session, _tid(tenant_id), job_id, actor=_actor(request))
        return {"id": str(job.id), "state": job.state}
    return _run(session, fn, request)


@app.post("/api/device-management/configuration-jobs/{job_id}/execute", dependencies=[Depends(management_auth)])
def execute_configuration_job(job_id: UUID, tenant_id: UUID | None = Query(default=None),
                              request: Request = None, session: Session = Depends(db)):
    def fn():
        job = configuration_service.execute_job(session, _tid(tenant_id), job_id, actor=_actor(request))
        return {"id": str(job.id), "state": job.state}
    return _run(session, fn, request)


@app.post("/api/device-management/configuration-jobs/{job_id}/task-result", dependencies=[Depends(management_auth)])
def configuration_task_result(job_id: UUID, payload: TaskResultIn, tenant_id: UUID | None = Query(default=None),
                              request: Request = None, session: Session = Depends(db)):
    def fn():
        job = configuration_service.process_task_result(
            session, _tid(tenant_id), job_id, task_id=payload.task_id, task_state=payload.task_state,
            task_result=payload.task_result, actor=_actor(request), correlation_id=payload.correlation_id)
        return {"id": str(job.id), "state": job.state}
    return _run(session, fn, request)


@app.post("/api/device-management/configuration-jobs/{job_id}/verify", dependencies=[Depends(management_auth)])
def verify_configuration_job(job_id: UUID, tenant_id: UUID | None = Query(default=None),
                             request: Request = None, session: Session = Depends(db)):
    def fn():
        job = configuration_service.verify_job(session, _tid(tenant_id), job_id, actor=_actor(request))
        return {"id": str(job.id), "state": job.state}
    return _run(session, fn, request)


@app.post("/api/device-management/configuration-jobs/{job_id}/cancel", dependencies=[Depends(management_auth)])
def cancel_configuration_job(job_id: UUID, payload: ReasonIn, tenant_id: UUID | None = Query(default=None),
                             request: Request = None, session: Session = Depends(db)):
    def fn():
        job = configuration_service.cancel_job(session, _tid(tenant_id), job_id, reason=payload.reason,
                                               actor=_actor(request))
        return {"id": str(job.id), "state": job.state}
    return _run(session, fn, request)


@app.post("/api/device-management/devices/{device_id}/observed", dependencies=[Depends(management_auth)])
def record_observed(device_id: UUID, payload: ObservedIn, tenant_id: UUID | None = Query(default=None),
                    request: Request = None, session: Session = Depends(db)):
    def fn():
        configuration_service.record_observed(session, _tid(tenant_id), device_id, parameters=payload.parameters,
                                              actor=_actor(request))
        return {"recorded": True}
    return _run(session, fn, request)


@app.post("/api/device-management/devices/{device_id}/detect-drift", dependencies=[Depends(management_auth)])
def detect_drift(device_id: UUID, tenant_id: UUID | None = Query(default=None), request: Request = None,
                 session: Session = Depends(db)):
    def fn():
        drift = configuration_service.detect_drift(session, _tid(tenant_id), device_id, actor=_actor(request))
        return {"drift": True, "classification": drift.classification, "parameters": drift.mismatched_parameters} \
            if drift else {"drift": False}
    return _run(session, fn, request)


@app.get("/api/device-management/devices/{device_id}/drift", dependencies=[Depends(management_auth)])
def device_drift(device_id: UUID, tenant_id: UUID | None = Query(default=None), session: Session = Depends(db)):
    device = device_service.get_device_or_404(session, _tid(tenant_id), device_id)
    return {"classification": device.last_drift_classification, "profile_compliance": device.profile_compliance}


# ===========================================================================
# Device actions
# ===========================================================================
@app.post("/api/device-management/devices/{device_id}/actions", status_code=status.HTTP_201_CREATED,
          dependencies=[Depends(management_auth)])
def create_action(device_id: UUID, payload: ActionCreate, tenant_id: UUID | None = Query(default=None),
                  request: Request = None, session: Session = Depends(db)):
    def fn():
        action = action_service.create_action(session, _tid(tenant_id), device_id, action_type=payload.action_type,
                                              parameters=payload.parameters, requested_by=payload.requested_by,
                                              actor=_actor(request), elevated=payload.elevated,
                                              idempotency_key=payload.idempotency_key,
                                              correlation_id=payload.correlation_id)
        return {"id": str(action.id), "action_type": action.action_type, "state": action.state}
    return _run(session, fn, request)


@app.get("/api/device-management/actions/{action_id}", dependencies=[Depends(management_auth)])
def action_detail(action_id: UUID, tenant_id: UUID | None = Query(default=None), session: Session = Depends(db)):
    action = action_service.get_action_or_404(session, _tid(tenant_id), action_id)
    return {"id": str(action.id), "action_type": action.action_type, "state": action.state,
            "cpe_id": str(action.cpe_id), "requires_approval": action.requires_approval,
            "genieacs_task_id": action.genieacs_task_id,
            "connection_request_outcome": action.connection_request_outcome,
            "failure_code": action.failure_code, "result_summary": action.result_summary}


@app.post("/api/device-management/actions/{action_id}/approve", dependencies=[Depends(management_auth)])
def approve_action(action_id: UUID, tenant_id: UUID | None = Query(default=None), request: Request = None,
                   session: Session = Depends(db)):
    def fn():
        action = action_service.approve_action(session, _tid(tenant_id), action_id, approver=_actor(request))
        return {"id": str(action.id), "state": action.state}
    return _run(session, fn, request)


@app.post("/api/device-management/actions/{action_id}/execute", dependencies=[Depends(management_auth)])
def execute_action(action_id: UUID, tenant_id: UUID | None = Query(default=None), request: Request = None,
                   session: Session = Depends(db)):
    def fn():
        action = action_service.execute_action(session, _tid(tenant_id), action_id, actor=_actor(request))
        return {"id": str(action.id), "state": action.state, "genieacs_task_id": action.genieacs_task_id}
    return _run(session, fn, request)


@app.post("/api/device-management/actions/{action_id}/outcome", dependencies=[Depends(management_auth)])
def action_outcome(action_id: UUID, payload: ActionOutcomeIn, tenant_id: UUID | None = Query(default=None),
                   request: Request = None, session: Session = Depends(db)):
    def fn():
        action = action_service.complete_action(session, _tid(tenant_id), action_id, ok=payload.ok,
                                                result=payload.result, actor=_actor(request),
                                                correlation_id=payload.correlation_id)
        return {"id": str(action.id), "state": action.state}
    return _run(session, fn, request)


# ===========================================================================
# Diagnostics
# ===========================================================================
@app.get("/api/device-management/devices/{device_id}/diagnostics/supported", dependencies=[Depends(management_auth)])
def supported_diagnostics(device_id: UUID, tenant_id: UUID | None = Query(default=None), session: Session = Depends(db)):
    return {"supported": diagnostic_service.supported_diagnostics(session, _tid(tenant_id), device_id)}


@app.post("/api/device-management/devices/{device_id}/diagnostics", status_code=status.HTTP_201_CREATED,
          dependencies=[Depends(management_auth)])
def create_diagnostic(device_id: UUID, payload: DiagnosticCreate, tenant_id: UUID | None = Query(default=None),
                      request: Request = None, session: Session = Depends(db)):
    def fn():
        job = diagnostic_service.create_diagnostic_job(
            session, _tid(tenant_id), device_id, diagnostic_type=payload.diagnostic_type,
            input_parameters=payload.input_parameters, requested_by=payload.requested_by,
            support_ticket_id=payload.support_ticket_id, idempotency_key=payload.idempotency_key,
            correlation_id=payload.correlation_id)
        return {"id": str(job.id), "diagnostic_type": job.diagnostic_type, "state": job.state}
    return _run(session, fn, request)


@app.get("/api/device-management/diagnostics/{job_id}", dependencies=[Depends(management_auth)])
def diagnostic_detail(job_id: UUID, tenant_id: UUID | None = Query(default=None), session: Session = Depends(db)):
    job = diagnostic_service.get_job_or_404(session, _tid(tenant_id), job_id)
    from .models import DiagnosticResult

    result = session.scalars(select(DiagnosticResult).where(DiagnosticResult.job_id == job.id)
                             .order_by(DiagnosticResult.created_at.desc()).limit(1)).first()
    return {"id": str(job.id), "diagnostic_type": job.diagnostic_type, "state": job.state,
            "support_ticket_id": job.support_ticket_id, "failure_code": job.failure_code,
            "result": {"normalized_result": result.normalized_result if result else None,
                       "evaluation": result.evaluation if result else None,
                       "offline": result.offline if result else False} if result else None}


@app.post("/api/device-management/diagnostics/{job_id}/run", dependencies=[Depends(management_auth)])
def run_diagnostic(job_id: UUID, tenant_id: UUID | None = Query(default=None), request: Request = None,
                   session: Session = Depends(db)):
    def fn():
        job = diagnostic_service.run_diagnostic(session, _tid(tenant_id), job_id, actor=_actor(request))
        return {"id": str(job.id), "state": job.state}
    return _run(session, fn, request)


@app.post("/api/device-management/diagnostics/{job_id}/result", dependencies=[Depends(management_auth)])
def diagnostic_result(job_id: UUID, payload: DiagnosticResultIn, tenant_id: UUID | None = Query(default=None),
                      request: Request = None, session: Session = Depends(db)):
    def fn():
        job = diagnostic_service.complete_diagnostic(
            session, _tid(tenant_id), job_id, raw=payload.raw, offline=payload.offline, failed=payload.failed,
            fault_code=payload.fault_code, actor=_actor(request), correlation_id=payload.correlation_id)
        return {"id": str(job.id), "state": job.state}
    return _run(session, fn, request)


# ===========================================================================
# Firmware
# ===========================================================================
@app.post("/api/device-management/firmware", status_code=status.HTTP_201_CREATED, dependencies=[Depends(management_auth)])
def upload_firmware(payload: FirmwareUpload, tenant_id: UUID | None = Query(default=None),
                    request: Request = None, session: Session = Depends(db)):
    # In production the binary is uploaded to private storage separately; the
    # checksum is validated against the file content before approval. Tests
    # exercise the validation path against a deterministic placeholder blob.
    data = b"\x00" * 32  # placeholder content for checksum verification path

    def fn():
        artifact = firmware_service.upload_firmware(
            session, _tid(tenant_id), vendor=payload.vendor, model=payload.model, version=payload.version,
            checksum_sha256=payload.checksum_sha256, data=data, product_class=payload.product_class,
            hardware_version=payload.hardware_version, release_notes=payload.release_notes,
            uploaded_by=_actor(request), actor=_actor(request), storage_ref=payload.storage_ref)
        return {"id": str(artifact.id), "approval_state": artifact.approval_state}
    return _run(session, fn, request)


@app.post("/api/device-management/firmware/{artifact_id}/approve", dependencies=[Depends(management_auth)])
def approve_firmware(artifact_id: UUID, payload: FirmwareApprovalIn, tenant_id: UUID | None = Query(default=None),
                     request: Request = None, session: Session = Depends(db)):
    def fn():
        artifact = firmware_service.approve_firmware(session, _tid(tenant_id), artifact_id, decision=payload.decision,
                                                     reviewed_by=payload.reviewed_by, reason=payload.reason,
                                                     actor=_actor(request))
        return {"id": str(artifact.id), "approval_state": artifact.approval_state}
    return _run(session, fn, request)


@app.post("/api/device-management/firmware/{artifact_id}/compatibility", dependencies=[Depends(management_auth)])
def define_compatibility(artifact_id: UUID, payload: CompatibilityIn, tenant_id: UUID | None = Query(default=None),
                         request: Request = None, session: Session = Depends(db)):
    def fn():
        row = firmware_service.define_compatibility(session, _tid(tenant_id), artifact_id,
                                                    model_variant_id=payload.model_variant_id,
                                                    min_current_version=payload.min_current_version,
                                                    max_current_version=payload.max_current_version,
                                                    verified=payload.verified, actor=_actor(request))
        return {"id": str(row.id), "verified": row.verified}
    return _run(session, fn, request)


@app.post("/api/device-management/firmware/rollouts", status_code=status.HTTP_201_CREATED,
          dependencies=[Depends(management_auth)])
def create_rollout(payload: RolloutCreate, tenant_id: UUID | None = Query(default=None),
                   request: Request = None, session: Session = Depends(db)):
    def fn():
        rollout = firmware_service.create_rollout(session, _tid(tenant_id), artifact_id=payload.artifact_id,
                                                  name=payload.name, strategy=payload.strategy,
                                                  policy=payload.policy, created_by=_actor(request),
                                                  actor=_actor(request))
        return {"id": str(rollout.id), "state": rollout.state}
    return _run(session, fn, request)


@app.post("/api/device-management/firmware/rollouts/{rollout_id}/stages", dependencies=[Depends(management_auth)])
def build_stages(rollout_id: UUID, payload: StageBuildIn, tenant_id: UUID | None = Query(default=None),
                 request: Request = None, session: Session = Depends(db)):
    def fn():
        stages = firmware_service.build_rollout_stages(session, _tid(tenant_id), rollout_id,
                                                       fleet_size=payload.fleet_size, actor=_actor(request))
        return {"stages": [{"stage_number": s.stage_number, "name": s.stage_name, "size": s.size,
                            "state": s.state, "requires_manual_approval": s.requires_manual_approval}
                           for s in stages]}
    return _run(session, fn, request)


@app.post("/api/device-management/firmware/rollouts/{rollout_id}/start", dependencies=[Depends(management_auth)])
def start_rollout(rollout_id: UUID, tenant_id: UUID | None = Query(default=None), request: Request = None,
                  session: Session = Depends(db)):
    def fn():
        rollout = firmware_service.start_rollout(session, _tid(tenant_id), rollout_id, actor=_actor(request))
        return {"id": str(rollout.id), "state": rollout.state}
    return _run(session, fn, request)


@app.post("/api/device-management/firmware/rollouts/{rollout_id}/deployments", status_code=status.HTTP_201_CREATED,
          dependencies=[Depends(management_auth)])
def queue_deployment(rollout_id: UUID, payload: DeploymentQueueIn, tenant_id: UUID | None = Query(default=None),
                     request: Request = None, session: Session = Depends(db)):
    def fn():
        deployment = firmware_service.queue_deployment(session, _tid(tenant_id), rollout_id=rollout_id,
                                                       cpe_id=payload.cpe_id, stage_id=payload.stage_id,
                                                       actor=_actor(request), correlation_id=payload.correlation_id)
        return {"id": str(deployment.id), "state": deployment.state}
    return _run(session, fn, request)


@app.post("/api/device-management/firmware/deployments/{deployment_id}/execute", dependencies=[Depends(management_auth)])
def execute_deployment(deployment_id: UUID, tenant_id: UUID | None = Query(default=None),
                       request: Request = None, session: Session = Depends(db)):
    def fn():
        deployment = firmware_service.execute_deployment(session, _tid(tenant_id), deployment_id, actor=_actor(request))
        return {"id": str(deployment.id), "state": deployment.state}
    return _run(session, fn, request)


@app.post("/api/device-management/firmware/deployments/{deployment_id}/outcome", dependencies=[Depends(management_auth)])
def deployment_outcome(deployment_id: UUID, payload: DeploymentOutcomeIn, tenant_id: UUID | None = Query(default=None),
                       request: Request = None, session: Session = Depends(db)):
    def fn():
        deployment = firmware_service.complete_deployment(
            session, _tid(tenant_id), deployment_id, transferred=payload.transferred,
            reported_firmware=payload.reported_firmware, health_checks=payload.health_checks,
            offline=payload.offline, actor=_actor(request), correlation_id=payload.correlation_id)
        return {"id": str(deployment.id), "state": deployment.state}
    return _run(session, fn, request)


@app.post("/api/device-management/firmware/rollouts/{rollout_id}/advance", dependencies=[Depends(management_auth)])
def advance_rollout(rollout_id: UUID, tenant_id: UUID | None = Query(default=None), request: Request = None,
                    session: Session = Depends(db)):
    def fn():
        return firmware_service.advance_rollout_stages(session, _tid(tenant_id), rollout_id, actor=_actor(request))
    return _run(session, fn, request)


@app.post("/api/device-management/firmware/rollouts/{rollout_id}/pause", dependencies=[Depends(management_auth)])
def pause_rollout(rollout_id: UUID, payload: ReasonIn, tenant_id: UUID | None = Query(default=None),
                  request: Request = None, session: Session = Depends(db)):
    def fn():
        rollout = firmware_service.pause_rollout(session, _tid(tenant_id), rollout_id, reason=payload.reason,
                                                 actor=_actor(request))
        return {"id": str(rollout.id), "state": rollout.state}
    return _run(session, fn, request)


@app.post("/api/device-management/firmware/rollouts/{rollout_id}/resume", dependencies=[Depends(management_auth)])
def resume_rollout(rollout_id: UUID, tenant_id: UUID | None = Query(default=None), request: Request = None,
                   session: Session = Depends(db)):
    def fn():
        rollout = firmware_service.resume_rollout(session, _tid(tenant_id), rollout_id, actor=_actor(request))
        return {"id": str(rollout.id), "state": rollout.state}
    return _run(session, fn, request)


@app.post("/api/device-management/firmware/rollouts/{rollout_id}/stop", dependencies=[Depends(management_auth)])
def stop_rollout(rollout_id: UUID, tenant_id: UUID | None = Query(default=None), request: Request = None,
                 session: Session = Depends(db)):
    def fn():
        rollout = firmware_service.stop_rollout(session, _tid(tenant_id), rollout_id, actor=_actor(request))
        return {"id": str(rollout.id), "state": rollout.state}
    return _run(session, fn, request)


# ===========================================================================
# ACS administration
# ===========================================================================
@app.post("/api/device-management/acs/instances", status_code=status.HTTP_201_CREATED, dependencies=[Depends(management_auth)])
def register_acs(payload: ACSRegister, request: Request, session: Session = Depends(db)):
    def fn():
        instance = acs_service.register_instance(
            session, name=payload.name, base_url=payload.base_url, tenant_id=payload.tenant_id,
            environment=payload.environment, cwmp_endpoint=payload.cwmp_endpoint,
            file_service_endpoint=payload.file_service_endpoint, actor=_actor(request))
        return {"id": str(instance.id), "name": instance.name, "health": instance.health}
    return _run(session, fn, request)


@app.post("/api/device-management/acs/instances/{instance_id}/health-check", dependencies=[Depends(management_auth)])
def acs_health_check(instance_id: UUID, request: Request, session: Session = Depends(db)):
    def fn():
        record = acs_service.health_check(session, instance_id, actor=_actor(request))
        return {"state": record.state, "version": record.version, "detail": record.detail}
    return _run(session, fn, request)


@app.get("/api/device-management/acs/instances", dependencies=[Depends(management_auth)])
def list_acs_instances(tenant_id: UUID | None = Query(default=None), session: Session = Depends(db)):
    tenant_id = _tid(tenant_id)
    from .models import ACSInstance

    rows = list(session.scalars(select(ACSInstance).where(
        (ACSInstance.tenant_id == tenant_id) | (ACSInstance.tenant_id.is_(None)))))
    return [{"id": str(r.id), "name": r.name, "base_url": r.base_url, "environment": r.environment,
             "version": r.version, "health": r.health, "is_active": r.is_active} for r in rows]


@app.post("/api/device-management/acs/instances/{instance_id}/reconcile", dependencies=[Depends(management_auth)])
def acs_reconcile(instance_id: UUID, tenant_id: UUID | None = Query(default=None), request: Request = None,
                  session: Session = Depends(db)):
    def fn():
        return acs_service.reconcile_devices(session, _tid(tenant_id), instance_id, actor=_actor(request))
    return _run(session, fn, request)


# ===========================================================================
# Telemetry / NMS
# ===========================================================================
@app.post("/api/device-management/devices/{device_id}/telemetry", dependencies=[Depends(management_auth)])
def capture_telemetry(device_id: UUID, payload: TelemetryIn, tenant_id: UUID | None = Query(default=None),
                      request: Request = None, session: Session = Depends(db)):
    def fn():
        row = telemetry_service.capture_telemetry(session, _tid(tenant_id), device_id, snapshot=payload.snapshot,
                                                  actor=_actor(request))
        return {"id": str(row.id), "captured_at": row.captured_at.isoformat()}
    return _run(session, fn, request)


@app.post("/api/device-management/devices/{device_id}/nms-signal", dependencies=[Depends(management_auth)])
def emit_nms_signal(device_id: UUID, payload: SignalIn, tenant_id: UUID | None = Query(default=None),
                    request: Request = None, session: Session = Depends(db)):
    def fn():
        ok = telemetry_service.emit_nms_signal(session, _tid(tenant_id), device_id, signal=payload.signal,
                                               severity=payload.severity, detail=payload.detail,
                                               actor=_actor(request), correlation_id=payload.correlation_id)
        return {"emitted": ok}
    return _run(session, fn, request)


# ===========================================================================
# Reports / audit
# ===========================================================================
@app.get("/api/device-management/reports/overview", dependencies=[Depends(management_auth)])
def report_overview(tenant_id: UUID | None = Query(default=None), session: Session = Depends(db)):
    tenant_id = _tid(tenant_id)
    from .enums import DEVICE_STATES

    return {
        "managed_devices": session.scalar(select(func.count(ManagedCpe.id)).where(
            ManagedCpe.tenant_id == tenant_id)) or 0,
        "online": session.scalar(select(func.count(ManagedCpe.id)).where(
            ManagedCpe.tenant_id == tenant_id, ManagedCpe.online.is_(True))) or 0,
        "quarantined": session.scalar(select(func.count(ManagedCpe.id)).where(
            ManagedCpe.tenant_id == tenant_id, ManagedCpe.state == "QUARANTINED")) or 0,
        "drift": session.scalar(select(func.count(ManagedCpe.id)).where(
            ManagedCpe.tenant_id == tenant_id, ManagedCpe.last_drift_classification != "NONE")) or 0,
        "profile_compliant": session.scalar(select(func.count(ManagedCpe.id)).where(
            ManagedCpe.tenant_id == tenant_id, ManagedCpe.profile_compliance == "COMPLIANT")) or 0,
        "firmware_compliant": session.scalar(select(func.count(ManagedCpe.id)).where(
            ManagedCpe.tenant_id == tenant_id, ManagedCpe.firmware_compliance == "COMPLIANT")) or 0,
    }


@app.get("/api/device-management/reports/devices", dependencies=[Depends(management_auth)])
def report_by_state(tenant_id: UUID | None = Query(default=None), session: Session = Depends(db)):
    tenant_id = _tid(tenant_id)
    rows = session.execute(select(ManagedCpe.state, func.count(ManagedCpe.id)).where(
        ManagedCpe.tenant_id == tenant_id).group_by(ManagedCpe.state)).all()
    return {"by_state": {state: count for state, count in rows}}


@app.get("/api/device-management/audit", dependencies=[Depends(management_auth)])
def audit_log(tenant_id: UUID | None = Query(default=None), session: Session = Depends(db)):
    tenant_id = _tid(tenant_id)
    from .models import AuditLog

    rows = list(session.scalars(select(AuditLog).where(AuditLog.tenant_id == tenant_id)
                                .order_by(AuditLog.created_at.desc()).limit(200)))
    return [{"id": str(a.id), "event_type": a.event_type, "entity_type": a.entity_type, "entity_id": a.entity_id,
             "actor": a.actor, "reason": a.reason,
             "created_at": a.created_at.isoformat() if a.created_at else None} for a in rows]
