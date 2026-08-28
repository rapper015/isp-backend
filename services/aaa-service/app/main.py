"""Private AAA API; no FreeRADIUS process, configuration, or networking is managed here."""
from contextlib import asynccontextmanager
from os import getenv
from uuid import UUID
import hashlib, secrets
import bcrypt
from datetime import datetime, timedelta, timezone
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from .database import Base, SessionLocal, engine
from .models import AccountingEvent, ActiveSession, AuditLog, Credential, IpLease, IpPool, Nas, NasCapability, NasChangePlan, NasCredential, NasDesiredConfiguration, NasHealthCheck, NasJob, NasRadiusAssignment, NasRemoteObject, NasSecretReveal, NasSecretRotation, NasSnapshot, RadiusCommand, RadiusServer, RadiusServerGroup, Tenant, UsageProjection
from .policy import calculate_policy
from .ipam import InvalidPool, validate_pool
from .radius import AttributeValidationError, normalize_attributes, normalize_mac, normalize_username
from .schemas import AccountingRequest, AuthenticationRequest, AuthorizationRequest, CoAIn, CredentialIn, CredentialUpdateIn, HeartbeatIn, IpPoolIn, IpReservationIn, NasCredentialIn, NasCredentialRotateIn, NasDesiredConfigurationIn, NasDraftIn, NasIn, NasPlanApplyIn, NasRadiusAssignmentIn, NasRadiusAssignmentUpdateIn, NasReconcileIn, NasRegistrationConfirmIn, NasRegistrationVerifyIn, NasRollbackIn, NasUpdateManagementIn, NasUpdateIn, NasVerifyIn, PasswordRotationIn, PolicyPreviewIn, PostAuthRequest, QuotaResetIn, RadiusResponse, RadiusServerGroupIn, RadiusServerGroupUpdateIn, RadiusServerIn, RadiusServerUpdateIn, SessionReconcileIn, TenantIn
from .security import decrypt_secret, encrypt_secret, hash_api_key, internal_service_auth, new_shared_secret
from .services import accounting, audit, authenticate, authorize, correlation, outbox
from .reconciliation import reconcile_nas_sessions
from .metrics import increment, snapshot
from .routeros import redact, validate_management_address
from .nas_planning import build_plan, configuration_hash, sanitize_configuration
from .nas_lifecycle import transition as lifecycle_transition
from .nas_service import build_adapter, test_nas_connection, discover_nas, apply_nas_configuration, verify_nas_configuration, run_nas_health_check, validate_nas_management_inputs, safe_routeros_error
from .nas_registration import confirm_manual_registration, generate_registration_package, record_technical_verification, registration_package_text, reveal_registration_package as reveal_package
from .nas_rotation import apply_secret_to_router, confirm_freeradius_update, expire_old_secret, reveal_rotation_secret, rollback_secret, rotation_registration_package, start_secret_rotation, verify_rotation
from .nas_drift import detect_drift
from .workers import process_nas_job, queue_nas_job
from .network_control.router import router as network_control_router

@asynccontextmanager
async def lifespan(_: FastAPI):
    # Production schema changes are applied through Alembic.  SQLite remains
    # convenient for isolated contract tests and explicitly opted-in local use.
    if getenv("AAA_AUTO_CREATE_SCHEMA", "").lower() == "true" or str(engine.url).startswith("sqlite"):
        Base.metadata.create_all(bind=engine)
    yield

app = FastAPI(title="AAA Service (private)", version="1.0.0", docs_url="/internal/docs", openapi_url="/internal/openapi.json", lifespan=lifespan)
@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Correlation-Id", "")[:64] or correlation(None)
    maximum = int(getenv("AAA_REQUEST_MAX_BYTES", "65536"))
    if request.headers.get("content-length", "0").isdigit() and int(request.headers.get("content-length", "0")) > maximum:
        increment("aaa_http_4xx_total")
        return JSONResponse(status_code=413, content={"detail": "request body too large"}, headers={"X-Correlation-Id": request_id})
    try:
        response = await call_next(request)
    except Exception:
        increment("aaa_http_errors_total")
        raise
    response.headers["X-Correlation-Id"] = request_id
    increment(f"aaa_http_{response.status_code // 100}xx_total")
    return response
def db():
    session = SessionLocal()
    try: yield session
    finally: session.close()

app.include_router(network_control_router)
def decision_response(decision: str, reply: dict, request_id: str) -> RadiusResponse:
    return RadiusResponse(outcome="Access-Accept" if decision == "ACCEPT" else "Access-Reject", decision=decision, reply_attributes=reply, correlation_id=request_id)
def attrs(payload):
    try: return normalize_attributes(payload.attributes)
    except AttributeValidationError as error: raise HTTPException(422, detail=str(error)) from error
def bounded(limit: int) -> int: return min(max(limit, 1), 100)
def tenant_item(session: Session, model, item_id: UUID, tenant_id: UUID, label: str):
    item = session.scalar(select(model).where(model.id == item_id, model.tenant_id == tenant_id))
    if not item: raise HTTPException(404, f"{label} not found")
    return item
def record_audit(session: Session, tenant_id: UUID | None, action: str, target: str, detail: dict) -> str:
    request_id = correlation(None); audit(session, tenant_id, action, target, request_id, detail); return request_id

def safe_desired(item: NasDesiredConfiguration) -> dict:
    return {"id": str(item.id), "version": item.version, "configuration": item.configuration, "status": item.status, "created_at": item.created_at.isoformat() if item.created_at else None}

def safe_plan(item: NasChangePlan) -> dict:
    return {"id": str(item.id), "nas_id": str(item.nas_id), "desired_configuration_id": str(item.desired_configuration_id), "planned_changes": item.planned_changes, "risk": item.risk, "validation": item.validation, "requires_approval": item.requires_approval, "status": item.status, "expires_at": item.expires_at.isoformat(), "applied_at": item.applied_at.isoformat() if item.applied_at else None}

def safe_nas(item: Nas) -> dict:
    return {
        "id": str(item.id), "tenant_id": str(item.tenant_id), "name": item.name, "short_name": item.short_name,
        "description": item.description, "site": item.site, "management_host": item.management_host,
        "management_port": item.management_port, "management_protocol": item.management_protocol, "api_mode": item.api_mode,
        "tls_verify": item.tls_verify, "source_ip": item.source_ip, "source_cidr": item.source_cidr,
        "radius_source_ipv6": item.radius_source_ipv6, "nas_identifier": item.nas_identifier, "vendor": item.vendor,
        "model": item.model, "serial_number": item.serial_number, "device_type": item.device_type,
        "routeros_version": item.routeros_version, "architecture": item.architecture, "board_name": item.board_name,
        "identity": item.identity, "time_zone": item.time_zone, "enabled": item.enabled,
        "lifecycle_status": item.lifecycle_status, "connection_status": item.connection_status,
        "configuration_status": item.configuration_status, "registration_status": item.registration_status,
        "health": item.health, "last_connected_at": item.last_connected_at, "last_discovery_at": item.last_discovery_at,
        "last_configuration_at": item.last_configuration_at, "last_verified_at": item.last_verified_at,
        "last_auth_at": item.last_auth_at, "last_accounting_at": item.last_accounting_at, "last_coa_at": item.last_coa_at,
        "failure_reason": item.failure_reason, "created_at": item.created_at, "updated_at": item.updated_at,
    }

def safe_assignment(item: NasRadiusAssignment) -> dict:
    server = None
    return {
        "id": str(item.id), "radius_server_id": str(item.radius_server_id), "priority": item.priority, "role": item.role,
        "services": item.services, "auth_port": item.auth_port, "accounting_port": item.accounting_port, "coa_port": item.coa_port,
        "timeout_seconds": item.timeout_seconds, "source_address": item.source_address, "secret_version": item.secret_version,
        "desired_status": item.desired_status, "applied_status": item.applied_status, "registration_status": item.registration_status,
        "remote_object_id": item.remote_object_id, "manual_confirmed": item.manual_confirmed,
        "last_synchronized_at": item.last_synchronized_at, "last_verified_at": item.last_verified_at,
        "failure_reason": item.failure_reason,
    }

def assignment_item(session: Session, nas_id: UUID, assignment_id: UUID, tenant_id: UUID) -> NasRadiusAssignment:
    nas = tenant_item(session, Nas, nas_id, tenant_id, "NAS")
    item = session.scalar(select(NasRadiusAssignment).where(NasRadiusAssignment.id == assignment_id, NasRadiusAssignment.nas_id == nas.id))
    if not item: raise HTTPException(404, "RADIUS assignment not found")
    return item

def active_credential(session: Session, nas_id: UUID) -> NasCredential | None:
    return session.scalar(select(NasCredential).where(NasCredential.nas_id == nas_id, NasCredential.status == "active").order_by(NasCredential.created_at.desc()).limit(1))

def enqueue_or_run(session: Session, nas_id: UUID, job_type: str, idempotency_key: str, sync: bool = False, safe_result: dict | None = None) -> dict:
    """Queue a NAS job idempotently; process inline when sync is requested."""
    request_id = correlation(None)
    job = queue_nas_job(session, nas_id, job_type, idempotency_key, request_id, safe_result or {})
    if job is None:
        existing = session.scalar(select(NasJob).where(NasJob.nas_id == nas_id, NasJob.idempotency_key == idempotency_key))
        if existing: return {"job_id": str(existing.id), "status": existing.status, "duplicate": True, "correlation_id": existing.correlation_id}
        raise HTTPException(409, "unable to queue job")
    session.commit()
    if sync:
        process_nas_job(session, job.id)
        session.commit()
        return {"job_id": str(job.id), "status": job.status, "duplicate": False, "correlation_id": request_id, "sync": True, "result": job.safe_result.get("result", job.safe_result)}
    return {"job_id": str(job.id), "status": job.status, "duplicate": False, "correlation_id": request_id}

@app.get("/health")
def health(): return {"status": "ok", "service": getenv("SERVICE_NAME", "aaa-service")}
@app.get("/status")
def service_status(): return {"service": "aaa", "phase": "radius-integration-api"}
@app.get("/internal/radius/v1/health", dependencies=[Depends(internal_service_auth)])
def radius_health(): return {"status": "ok", "service": "aaa", "freeradius_managed": False}
@app.get("/internal/radius/v1/readiness", dependencies=[Depends(internal_service_auth)])
def readiness(session: Session = Depends(db)):
    session.execute(select(Tenant.id).limit(1)); return {"status": "ready", "database": "ok"}
@app.get("/internal/radius/v1/metrics", dependencies=[Depends(internal_service_auth)])
def metrics(): return {"service": "aaa", "metrics": snapshot()}

@app.post("/internal/radius/v1/authenticate", response_model=RadiusResponse, dependencies=[Depends(internal_service_auth)])
def internal_authenticate(payload: AuthenticationRequest, request: Request, session: Session = Depends(db)):
    attributes, _ = attrs(payload); request_id = correlation(payload.correlation_id or request.headers.get("X-Correlation-Id"))
    decision, reply = authenticate(session, attributes, request_id); increment("aaa_authentication_accepts_total" if decision == "ACCEPT" else "aaa_authentication_rejects_total"); session.commit(); return decision_response(decision, reply, request_id)
@app.post("/internal/radius/v1/authorize", response_model=RadiusResponse, dependencies=[Depends(internal_service_auth)])
def internal_authorize(payload: AuthorizationRequest, request: Request, session: Session = Depends(db)):
    attributes, _ = attrs(payload); request_id = correlation(payload.correlation_id or request.headers.get("X-Correlation-Id"))
    decision, reply = authorize(session, attributes, request_id); increment("aaa_authorization_accepts_total" if decision == "ACCEPT" else "aaa_authorization_rejects_total"); session.commit(); return decision_response(decision, reply, request_id)
@app.post("/internal/radius/v1/accounting", response_model=RadiusResponse, dependencies=[Depends(internal_service_auth)])
def internal_accounting(payload: AccountingRequest, request: Request, session: Session = Depends(db)):
    attributes, diagnostic = attrs(payload); request_id = correlation(payload.correlation_id or request.headers.get("X-Correlation-Id"))
    decision, durable = accounting(session, attributes, diagnostic, request_id, payload.idempotency_key)
    if not durable: increment("aaa_accounting_rejected_total"); session.rollback(); return RadiusResponse(outcome="Access-Reject", decision=decision, correlation_id=request_id)
    increment("aaa_accounting_duplicates_total" if decision == "DUPLICATE" else "aaa_accounting_events_total")
    session.commit(); return RadiusResponse(outcome="OK", decision=decision, correlation_id=request_id)
@app.post("/internal/radius/v1/post-auth", response_model=RadiusResponse, dependencies=[Depends(internal_service_auth)])
def post_auth(payload: PostAuthRequest): return RadiusResponse(outcome="OK", decision="ACKNOWLEDGED", correlation_id=correlation(payload.correlation_id))

@app.post("/api/aaa/tenants", dependencies=[Depends(internal_service_auth)])
def create_tenant(payload: TenantIn, session: Session = Depends(db)):
    tenant = Tenant(**payload.model_dump()); session.add(tenant); session.commit(); return {"id": str(tenant.id)}
@app.post("/api/nas", dependencies=[Depends(internal_service_auth)])
def create_nas_draft(payload: NasDraftIn, session: Session = Depends(db)):
    if not session.get(Tenant, payload.tenant_id): raise HTTPException(404, "tenant not found")
    validated, error = validate_nas_management_inputs(session, payload.tenant_id, payload.model_dump())
    if error: raise HTTPException(422, error)
    if payload.radius_group_id and not session.scalar(select(RadiusServerGroup).where(RadiusServerGroup.id == payload.radius_group_id, (RadiusServerGroup.tenant_id == payload.tenant_id) | RadiusServerGroup.tenant_id.is_(None))): raise HTTPException(422, "RADIUS group is not available to tenant")
    nas = Nas(tenant_id=payload.tenant_id, name=payload.name, short_name=payload.short_name or payload.name[:64], description=payload.description, site=payload.site, management_host=validated["management_host"], management_port=validated["management_port"], management_protocol=payload.management_protocol, api_mode=payload.api_mode, tls_verify=payload.tls_verify, source_ip=validated["radius_source_ip"], radius_source_ipv6=payload.radius_source_ipv6, nas_identifier=payload.nas_identifier, vendor=payload.vendor or "mikrotik", model=payload.model, device_type="routeros", radius_group_id=payload.radius_group_id, allowed_services=payload.services, health="unknown", lifecycle_status="DRAFT", connection_status="UNKNOWN", configuration_status="NONE", registration_status="NOT_REQUIRED", capabilities={"management_host": validated["management_host"], "management_port": validated["management_port"], "management_protocol": payload.management_protocol, "lifecycle": "DRAFT"})
    session.add(nas); session.flush()
    session.add(NasCredential(nas_id=nas.id, username_ciphertext=encrypt_secret(payload.routeros_username), secret_ciphertext=encrypt_secret(payload.routeros_password), api_port=payload.management_port, tls_settings=payload.tls_settings))
    request_id = record_audit(session, payload.tenant_id, "nas.draft_created", str(nas.id), {"management_host": validated["management_host"], "services": payload.services})
    session.commit(); return {"id": str(nas.id), "lifecycle_status": "DRAFT", "connection_status": "UNKNOWN", "correlation_id": request_id}

@app.get("/api/nas", dependencies=[Depends(internal_service_auth)])
def list_managed_nas(tenant_id: UUID, lifecycle_status: str | None = None, enabled: bool | None = None, health: str | None = None, q: str | None = None, sort: str = "name", limit: int = 100, offset: int = 0, session: Session = Depends(db)):
    statement = select(Nas).where(Nas.tenant_id == tenant_id)
    if lifecycle_status: statement = statement.where(Nas.lifecycle_status == lifecycle_status.upper())
    if enabled is not None: statement = statement.where(Nas.enabled.is_(enabled))
    if health: statement = statement.where(Nas.health == health)
    if q: statement = statement.where((Nas.name.ilike(f"%{q}%")) | (Nas.management_host.ilike(f"%{q}%")) | (Nas.source_ip.ilike(f"%{q}%")))
    order = {"name": Nas.name, "created_at": Nas.created_at, "updated_at": Nas.updated_at, "health": Nas.health}.get(sort, Nas.name)
    items = session.scalars(statement.order_by(order, Nas.id).offset(max(offset, 0)).limit(bounded(limit)))
    return [safe_nas(item) for item in items]

@app.get("/api/nas/{nas_id}", dependencies=[Depends(internal_service_auth)])
def get_managed_nas(nas_id: UUID, tenant_id: UUID, session: Session = Depends(db)):
    return safe_nas(tenant_item(session, Nas, nas_id, tenant_id, "NAS"))

@app.patch("/api/nas/{nas_id}", dependencies=[Depends(internal_service_auth)])
def update_managed_nas(nas_id: UUID, tenant_id: UUID, payload: NasUpdateManagementIn, session: Session = Depends(db)):
    item = tenant_item(session, Nas, nas_id, tenant_id, "NAS")
    updates = payload.model_dump(exclude_unset=True)
    if any(key in updates for key in ("management_host", "management_port", "radius_source_ip")):
        probe = {key: updates.get(key, getattr(item, {"management_host": "management_host", "management_port": "management_port", "radius_source_ip": "source_ip"}[key], None)) for key in ("management_host", "management_port", "radius_source_ip")}
        probe["id"] = item.id
        validated, error = validate_nas_management_inputs(session, tenant_id, probe)
        if error: raise HTTPException(422, error)
        if "management_host" in updates: item.management_host = validated["management_host"]
        if "radius_source_ip" in updates: item.source_ip = validated["radius_source_ip"]
    if "radius_source_ip" in updates: updates.pop("radius_source_ip", None)
    for key, value in updates.items():
        if key in {"management_host", "radius_source_ip"}: continue
        if hasattr(item, key): setattr(item, key, value)
    request_id = record_audit(session, tenant_id, "nas.updated", str(item.id), {"fields": sorted(payload.model_dump(exclude_unset=True))})
    session.commit(); return {**safe_nas(item), "correlation_id": request_id}

@app.delete("/api/nas/{nas_id}", dependencies=[Depends(internal_service_auth)])
def delete_managed_nas(nas_id: UUID, tenant_id: UUID, session: Session = Depends(db)):
    item = tenant_item(session, Nas, nas_id, tenant_id, "NAS")
    if item.lifecycle_status in {"CONFIGURING", "VERIFYING", "DECOMMISSIONING"}: raise HTTPException(409, "NAS has an operation in progress")
    if session.scalar(select(NasJob.id).where(NasJob.nas_id == item.id, NasJob.status.in_(["QUEUED", "RUNNING"])).limit(1)): raise HTTPException(409, "NAS has pending jobs")
    if session.scalar(select(ActiveSession.id).where(ActiveSession.nas_id == item.id, ActiveSession.status != "STOPPED").limit(1)): raise HTTPException(409, "cannot delete NAS with active sessions")
    request_id = record_audit(session, tenant_id, "nas.deleted", str(item.id), {"name": item.name}); session.delete(item); session.commit()
    return {"id": str(nas_id), "deleted": True, "correlation_id": request_id}

@app.post("/api/nas/{nas_id}/enable", dependencies=[Depends(internal_service_auth)])
def enable_managed_nas(nas_id: UUID, tenant_id: UUID, session: Session = Depends(db)):
    item = tenant_item(session, Nas, nas_id, tenant_id, "NAS")
    item.enabled = True
    if item.lifecycle_status == "DISABLED": item.lifecycle_status = lifecycle_transition("DISABLED", "CONNECTION_PENDING")
    request_id = record_audit(session, tenant_id, "nas.enabled", str(item.id), {}); session.commit()
    return {"id": str(item.id), "enabled": True, "lifecycle_status": item.lifecycle_status, "correlation_id": request_id}

@app.post("/api/nas/{nas_id}/disable", dependencies=[Depends(internal_service_auth)])
def disable_managed_nas(nas_id: UUID, tenant_id: UUID, session: Session = Depends(db)):
    item = tenant_item(session, Nas, nas_id, tenant_id, "NAS")
    item.enabled = False
    try: item.lifecycle_status = lifecycle_transition(item.lifecycle_status, "DISABLED")
    except ValueError: item.lifecycle_status = "DISABLED"
    request_id = record_audit(session, tenant_id, "nas.disabled", str(item.id), {}); session.commit()
    return {"id": str(item.id), "enabled": False, "lifecycle_status": item.lifecycle_status, "correlation_id": request_id}

@app.post("/api/nas/{nas_id}/decommission", dependencies=[Depends(internal_service_auth)])
def decommission_nas(nas_id: UUID, tenant_id: UUID, session: Session = Depends(db)):
    item = tenant_item(session, Nas, nas_id, tenant_id, "NAS")
    if item.lifecycle_status in {"CONFIGURING", "VERIFYING"}: raise HTTPException(409, "NAS has an operation in progress")
    try: item.lifecycle_status = lifecycle_transition(item.lifecycle_status, "DECOMMISSIONING")
    except ValueError: item.lifecycle_status = "DECOMMISSIONING"
    item.lifecycle_status = lifecycle_transition(item.lifecycle_status, "DECOMMISSIONED")
    item.enabled = False
    request_id = record_audit(session, tenant_id, "nas.decommissioned", str(item.id), {}); session.commit()
    return {"id": str(item.id), "lifecycle_status": "DECOMMISSIONED", "correlation_id": request_id}

@app.post("/api/nas/{nas_id}/credentials", dependencies=[Depends(internal_service_auth)])
def create_nas_credential(nas_id: UUID, tenant_id: UUID, payload: NasCredentialIn, session: Session = Depends(db)):
    item = tenant_item(session, Nas, nas_id, tenant_id, "NAS")
    session.query(NasCredential).filter(NasCredential.nas_id == item.id, NasCredential.status == "active").update({"status": "superseded"})
    credential = NasCredential(nas_id=item.id, username_ciphertext=encrypt_secret(payload.username), secret_ciphertext=encrypt_secret(payload.password), api_port=payload.api_port, tls_settings=payload.tls_settings, certificate_reference=payload.certificate_reference, status="active", last_rotated_at=datetime.now(timezone.utc))
    session.add(credential)
    request_id = record_audit(session, tenant_id, "nas.credential_created", str(item.id), {"api_port": payload.api_port, "tls_verify": bool(payload.tls_settings.get("verify", True))})
    session.commit(); return {"id": str(credential.id), "nas_id": str(item.id), "credential_type": "password", "created": True, "correlation_id": request_id}

@app.post("/api/nas/{nas_id}/credentials/rotate", dependencies=[Depends(internal_service_auth)])
def rotate_nas_credential(nas_id: UUID, tenant_id: UUID, payload: NasCredentialRotateIn, session: Session = Depends(db)):
    item = tenant_item(session, Nas, nas_id, tenant_id, "NAS")
    existing = session.scalar(select(NasCredential).where(NasCredential.nas_id == item.id, NasCredential.status == "active", NasCredential.created_at == datetime.now(timezone.utc)))
    session.query(NasCredential).filter(NasCredential.nas_id == item.id, NasCredential.status == "active").update({"status": "superseded"})
    credential = NasCredential(nas_id=item.id, username_ciphertext=encrypt_secret(payload.username or item.name), secret_ciphertext=encrypt_secret(payload.password), status="active", last_rotated_at=datetime.now(timezone.utc))
    session.add(credential)
    request_id = record_audit(session, tenant_id, "nas.credential_rotated", str(item.id), {"credential_id": str(credential.id)})
    session.commit(); return {"id": str(credential.id), "nas_id": str(item.id), "last_rotated_at": credential.last_rotated_at, "correlation_id": request_id}

@app.post("/api/nas/{nas_id}/test-connection", dependencies=[Depends(internal_service_auth)])
def test_connection(nas_id: UUID, tenant_id: UUID, idempotency_key: str, sync: bool = False, session: Session = Depends(db)):
    item = tenant_item(session, Nas, nas_id, tenant_id, "NAS")
    if item.lifecycle_status in {"CONFIGURING", "VERIFYING"}: raise HTTPException(409, "NAS has an operation in progress")
    try: item.lifecycle_status = lifecycle_transition(item.lifecycle_status, "CONNECTION_PENDING")
    except ValueError: pass
    try: item.lifecycle_status = lifecycle_transition(item.lifecycle_status, "CONNECTION_TESTING")
    except ValueError: pass
    request_id = record_audit(session, tenant_id, "nas.connection_test_requested", str(item.id), {})
    session.commit()
    return enqueue_or_run(session, item.id, "connection_test", idempotency_key, sync=sync, safe_result={"correlation_id": request_id})

@app.get("/api/nas/{nas_id}/connection-status", dependencies=[Depends(internal_service_auth)])
def connection_status(nas_id: UUID, tenant_id: UUID, session: Session = Depends(db)):
    item = tenant_item(session, Nas, nas_id, tenant_id, "NAS")
    return {"nas_id": str(item.id), "connection_status": item.connection_status, "health": item.health, "last_connected_at": item.last_connected_at, "failure_reason": item.failure_reason, "lifecycle_status": item.lifecycle_status}

@app.post("/api/nas/{nas_id}/discover", dependencies=[Depends(internal_service_auth)])
def discover_nas_api(nas_id: UUID, tenant_id: UUID, idempotency_key: str, sync: bool = False, session: Session = Depends(db)):
    item = tenant_item(session, Nas, nas_id, tenant_id, "NAS")
    if not item.management_host: raise HTTPException(422, "management address is required before discovery")
    if item.connection_status != "CONNECTED": raise HTTPException(409, "connection test must pass before discovery")
    try: item.lifecycle_status = lifecycle_transition(item.lifecycle_status, "DISCOVERING")
    except ValueError: pass
    request_id = record_audit(session, tenant_id, "nas.discovery_requested", str(item.id), {})
    session.commit()
    return enqueue_or_run(session, item.id, "discovery", idempotency_key, sync=sync, safe_result={"correlation_id": request_id})

@app.get("/api/nas/{nas_id}/capabilities", dependencies=[Depends(internal_service_auth)])
def get_nas_capabilities(nas_id: UUID, tenant_id: UUID, session: Session = Depends(db)):
    tenant_item(session, Nas, nas_id, tenant_id, "NAS")
    capability = session.scalar(select(NasCapability).where(NasCapability.nas_id == nas_id).order_by(NasCapability.detected_at.desc()).limit(1))
    return {"nas_id": str(nas_id), "capabilities": capability.flags if capability else {}, "detected_at": capability.detected_at if capability else None, "version": capability.version if capability else None}

@app.get("/api/nas/{nas_id}/current-radius-configuration", dependencies=[Depends(internal_service_auth)])
def current_radius_configuration(nas_id: UUID, tenant_id: UUID, session: Session = Depends(db)):
    tenant_item(session, Nas, nas_id, tenant_id, "NAS")
    snapshot = session.scalar(select(NasSnapshot).where(NasSnapshot.nas_id == nas_id).order_by(NasSnapshot.version.desc()).limit(1))
    if not snapshot: raise HTTPException(404, "no captured configuration available")
    config = snapshot.sanitized_configuration or {}
    return {"nas_id": str(nas_id), "version": snapshot.version, "captured_at": snapshot.created_at, "source": snapshot.source, "configuration_hash": snapshot.configuration_hash, "radius_entries": redact(config.get("radius_entries", [])), "ppp_aaa": config.get("ppp_aaa", {}), "user_aaa": config.get("user_aaa", {}), "hotspot_profiles": config.get("hotspot_profiles", []), "radius_incoming": config.get("radius_incoming", [])}

@app.get("/api/nas/{nas_id}/snapshots", dependencies=[Depends(internal_service_auth)])
def list_nas_snapshots(nas_id: UUID, tenant_id: UUID, limit: int = 20, offset: int = 0, session: Session = Depends(db)):
    tenant_item(session, Nas, nas_id, tenant_id, "NAS")
    return [{"id": str(item.id), "version": item.version, "scope": item.scope, "source": item.source, "configuration_hash": item.configuration_hash, "created_at": item.created_at} for item in session.scalars(select(NasSnapshot).where(NasSnapshot.nas_id == nas_id).order_by(NasSnapshot.version.desc()).offset(max(offset, 0)).limit(bounded(limit)))]
@app.post("/api/nas/{nas_id}/radius-assignments", dependencies=[Depends(internal_service_auth)])
def create_nas_radius_assignment(nas_id: UUID, tenant_id: UUID, payload: NasRadiusAssignmentIn, session: Session = Depends(db)):
    nas = tenant_item(session, Nas, nas_id, tenant_id, "NAS")
    server = session.get(RadiusServer, payload.radius_server_id)
    if not server or not server.enabled: raise HTTPException(422, "RADIUS server is not available")
    if session.scalar(select(NasRadiusAssignment.id).where(NasRadiusAssignment.nas_id == nas.id, NasRadiusAssignment.radius_server_id == server.id)): raise HTTPException(409, "RADIUS assignment already exists")
    assignment = NasRadiusAssignment(nas_id=nas.id, radius_server_id=server.id, priority=payload.priority, role=payload.role, services=payload.services, auth_port=payload.auth_port, accounting_port=payload.accounting_port, coa_port=payload.coa_port, timeout_seconds=payload.timeout_seconds, source_address=payload.source_address or nas.source_ip, secret_ciphertext=encrypt_secret(new_shared_secret()), secret_version=1, registration_status="DETAILS_GENERATED")
    session.add(assignment); request_id = record_audit(session, tenant_id, "nas.radius_assignment_created", str(assignment.id), {"radius_server_id": str(server.id), "role": payload.role}); outbox(session, "nas.radius_registration.generated.v1", tenant_id, request_id, {"nas_id": str(nas.id), "assignment_id": str(assignment.id), "radius_server_id": str(server.id)})
    session.commit(); return {"id": str(assignment.id), "registration_status": assignment.registration_status, "secret_displayed": False, "correlation_id": request_id}
@app.get("/api/nas/{nas_id}/radius-assignments", dependencies=[Depends(internal_service_auth)])
def list_nas_radius_assignments(nas_id: UUID, tenant_id: UUID, session: Session = Depends(db)):
    tenant_item(session, Nas, nas_id, tenant_id, "NAS")
    return [safe_assignment(item) for item in session.scalars(select(NasRadiusAssignment).where(NasRadiusAssignment.nas_id == nas_id).order_by(NasRadiusAssignment.priority))]
@app.patch("/api/nas/{nas_id}/radius-assignments/{assignment_id}", dependencies=[Depends(internal_service_auth)])
def update_nas_radius_assignment(nas_id: UUID, assignment_id: UUID, tenant_id: UUID, payload: NasRadiusAssignmentUpdateIn, session: Session = Depends(db)):
    item = assignment_item(session, nas_id, assignment_id, tenant_id)
    updates = payload.model_dump(exclude_unset=True)
    for key, value in updates.items():
        if hasattr(item, key): setattr(item, key, value)
    request_id = record_audit(session, tenant_id, "nas.radius_assignment_updated", str(item.id), {"fields": sorted(updates)})
    session.commit(); return {**safe_assignment(item), "correlation_id": request_id}
@app.delete("/api/nas/{nas_id}/radius-assignments/{assignment_id}", dependencies=[Depends(internal_service_auth)])
def delete_nas_radius_assignment(nas_id: UUID, assignment_id: UUID, tenant_id: UUID, session: Session = Depends(db)):
    item = assignment_item(session, nas_id, assignment_id, tenant_id)
    if item.applied_status in {"applied", "verified"}: raise HTTPException(409, "disable or roll back the applied assignment before deletion")
    request_id = record_audit(session, tenant_id, "nas.radius_assignment_deleted", str(item.id), {})
    session.delete(item); session.commit()
    return {"id": str(assignment_id), "deleted": True, "correlation_id": request_id}
@app.post("/api/nas/{nas_id}/radius-assignments/{assignment_id}/registration-package", dependencies=[Depends(internal_service_auth)])
def create_registration_reveal(nas_id: UUID, assignment_id: UUID, tenant_id: UUID, session: Session = Depends(db)):
    nas = tenant_item(session, Nas, nas_id, tenant_id, "NAS"); assignment = assignment_item(session, nas_id, assignment_id, tenant_id)
    try:
        package = generate_registration_package(session, nas, assignment)
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    request_id = record_audit(session, tenant_id, "nas.registration_package_issued", str(assignment.id), {"secret_version": assignment.secret_version, "registration_status": assignment.registration_status})
    session.commit()
    return {**package, "correlation_id": request_id}
@app.post("/api/nas/{nas_id}/radius-assignments/{assignment_id}/registration-package/reveal", dependencies=[Depends(internal_service_auth)])
def reveal_registration_package(nas_id: UUID, assignment_id: UUID, tenant_id: UUID, reveal_token: str, session: Session = Depends(db)):
    nas = tenant_item(session, Nas, nas_id, tenant_id, "NAS"); assignment = assignment_item(session, nas_id, assignment_id, tenant_id)
    try:
        package = reveal_package(session, nas, assignment, reveal_token)
    except ValueError as error:
        raise HTTPException(404, str(error)) from error
    request_id = record_audit(session, tenant_id, "nas.registration_package_accessed", str(assignment.id), {"secret_version": assignment.secret_version})
    session.commit()
    return {**package, "correlation_id": request_id, "text": registration_package_text(package)}
@app.post("/api/nas/{nas_id}/radius-assignments/{assignment_id}/confirm-registration", dependencies=[Depends(internal_service_auth)])
def confirm_registration(nas_id: UUID, assignment_id: UUID, tenant_id: UUID, payload: NasRegistrationConfirmIn, session: Session = Depends(db)):
    nas = tenant_item(session, Nas, nas_id, tenant_id, "NAS"); assignment = assignment_item(session, nas_id, assignment_id, tenant_id)
    try:
        result = confirm_manual_registration(session, nas, assignment, payload.model_dump())
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    request_id = record_audit(session, tenant_id, "nas.radius_registration_confirmed", str(assignment.id), {"confirmation": result["confirmation"]})
    outbox(session, "nas.radius_registration.confirmed.v1", tenant_id, request_id, {"nas_id": str(nas.id), "assignment_id": str(assignment.id), "registration_status": assignment.registration_status})
    session.commit(); return {**result, "registration_status": assignment.registration_status, "correlation_id": request_id}
@app.post("/api/nas/{nas_id}/radius-assignments/{assignment_id}/verify", dependencies=[Depends(internal_service_auth)])
def verify_registration(nas_id: UUID, assignment_id: UUID, tenant_id: UUID, payload: NasRegistrationVerifyIn, session: Session = Depends(db)):
    nas = tenant_item(session, Nas, nas_id, tenant_id, "NAS"); assignment = assignment_item(session, nas_id, assignment_id, tenant_id)
    try:
        result = record_technical_verification(session, nas, assignment, payload.signal, payload.detail)
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    request_id = record_audit(session, tenant_id, "nas.radius_registration_verified", str(assignment.id), {"signal": payload.signal})
    outbox(session, "nas.radius_registration.verified.v1", tenant_id, request_id, {"nas_id": str(nas.id), "assignment_id": str(assignment.id), "signal": payload.signal})
    session.commit(); return {**result, "correlation_id": request_id}
@app.get("/api/nas/{nas_id}/radius-registration-status", dependencies=[Depends(internal_service_auth)])
def radius_registration_status(nas_id: UUID, tenant_id: UUID, session: Session = Depends(db)):
    tenant_item(session, Nas, nas_id, tenant_id, "NAS")
    assignments = list(session.scalars(select(NasRadiusAssignment).where(NasRadiusAssignment.nas_id == nas_id).order_by(NasRadiusAssignment.priority)))
    return {"nas_id": str(nas_id), "assignments": [{"id": str(item.id), "registration_status": item.registration_status, "manual_confirmed": item.manual_confirmed, "secret_version": item.secret_version, "last_verified_at": item.last_verified_at} for item in assignments]}
@app.post("/api/nas/{nas_id}/radius-assignments/{assignment_id}/rotate-secret", dependencies=[Depends(internal_service_auth)])
def rotate_secret(nas_id: UUID, assignment_id: UUID, tenant_id: UUID, session: Session = Depends(db)):
    nas = tenant_item(session, Nas, nas_id, tenant_id, "NAS"); assignment = assignment_item(session, nas_id, assignment_id, tenant_id)
    rotation = start_secret_rotation(session, nas, assignment)
    request_id = record_audit(session, tenant_id, "nas.radius_secret_rotation_started", str(assignment.id), {"rotation_id": str(rotation.id), "new_secret_version": rotation.new_secret_version})
    outbox(session, "nas.radius_secret_rotation.requested.v1", tenant_id, request_id, {"nas_id": str(nas.id), "assignment_id": str(assignment.id), "rotation_id": str(rotation.id), "state": rotation.state})
    package = rotation_registration_package(session, rotation)
    session.commit()
    return {"rotation_id": str(rotation.id), "state": rotation.state, "new_secret_version": rotation.new_secret_version, "reveal_token": package["reveal_token"], "expires_in_seconds": package["expires_in_seconds"], "correlation_id": request_id}
@app.post("/api/nas/{nas_id}/radius-assignments/{assignment_id}/confirm-freeradius-update", dependencies=[Depends(internal_service_auth)])
def confirm_freeradius_secret_update(nas_id: UUID, assignment_id: UUID, tenant_id: UUID, rotation_id: UUID, session: Session = Depends(db)):
    assignment = assignment_item(session, nas_id, assignment_id, tenant_id)
    rotation = session.scalar(select(NasSecretRotation).where(NasSecretRotation.id == rotation_id, NasSecretRotation.assignment_id == assignment.id))
    if not rotation: raise HTTPException(404, "secret rotation not found")
    try:
        result = confirm_freeradius_update(session, rotation)
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    request_id = record_audit(session, tenant_id, "nas.radius_secret_freeradius_update_confirmed", str(assignment.id), {"rotation_id": str(rotation.id)})
    session.commit(); return {**result, "rotation_id": str(rotation.id), "correlation_id": request_id}
@app.post("/api/nas/{nas_id}/radius-assignments/{assignment_id}/apply-secret", dependencies=[Depends(internal_service_auth)])
def apply_secret(nas_id: UUID, assignment_id: UUID, tenant_id: UUID, rotation_id: UUID, sync: bool = True, session: Session = Depends(db)):
    assignment = assignment_item(session, nas_id, assignment_id, tenant_id)
    rotation = session.scalar(select(NasSecretRotation).where(NasSecretRotation.id == rotation_id, NasSecretRotation.assignment_id == assignment.id))
    if not rotation: raise HTTPException(404, "secret rotation not found")
    nas = session.get(Nas, nas_id)
    adapter = build_adapter(nas, active_credential(session, nas_id))
    try:
        result = apply_secret_to_router(session, rotation, adapter)
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    except Exception as error:  # noqa: BLE001 - structured adapter errors stay safe
        request_id = record_audit(session, tenant_id, "nas.radius_secret_apply_failed", str(assignment.id), {"rotation_id": str(rotation.id), "error": getattr(error, "code", "COMMAND_FAILED")})
        session.commit(); raise HTTPException(502, getattr(error, "code", "COMMAND_FAILED")) from error
    request_id = record_audit(session, tenant_id, "nas.radius_secret_applied_to_router", str(assignment.id), {"rotation_id": str(rotation.id), "remote_object_id": result.get("remote_object_id")})
    session.commit(); return {**result, "rotation_id": str(rotation.id), "correlation_id": request_id}
@app.post("/api/nas/{nas_id}/radius-assignments/{assignment_id}/rollback-secret", dependencies=[Depends(internal_service_auth)])
def rollback_secret_api(nas_id: UUID, assignment_id: UUID, tenant_id: UUID, rotation_id: UUID, session: Session = Depends(db)):
    assignment = assignment_item(session, nas_id, assignment_id, tenant_id)
    rotation = session.scalar(select(NasSecretRotation).where(NasSecretRotation.id == rotation_id, NasSecretRotation.assignment_id == assignment.id))
    if not rotation: raise HTTPException(404, "secret rotation not found")
    nas = session.get(Nas, nas_id)
    adapter = build_adapter(nas, active_credential(session, nas_id))
    try:
        result = rollback_secret(session, rotation, adapter)
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    request_id = record_audit(session, tenant_id, "nas.radius_secret_rolled_back", str(assignment.id), {"rotation_id": str(rotation.id)})
    session.commit(); return {**result, "rotation_id": str(rotation.id), "correlation_id": request_id}

@app.post("/api/nas/{nas_id}/desired-configuration", dependencies=[Depends(internal_service_auth)])
def create_desired_configuration(nas_id: UUID, tenant_id: UUID, payload: NasDesiredConfigurationIn, session: Session = Depends(db)):
    nas = tenant_item(session, Nas, nas_id, tenant_id, "NAS")
    configuration = sanitize_configuration({
        "services": payload.services,
        "ppp_aaa": payload.ppp_aaa,
        "accounting": payload.accounting,
        "hotspot_profiles": [item.model_dump() for item in payload.hotspot_profiles],
        "incoming_coa": payload.incoming_coa,
        "coa_port": payload.coa_port,
        "interim_update_seconds": payload.interim_update_seconds,
        "login_radius": payload.login_radius,
        "break_glass_verified": payload.break_glass_verified,
        "acknowledge_login_risk": payload.acknowledge_login_risk,
        "user_aaa_default_group": payload.user_aaa_default_group,
        "user_aaa_excluded_groups": payload.user_aaa_excluded_groups,
        "user_aaa_accounting": payload.user_aaa_accounting,
    })
    version = (session.scalar(select(func.max(NasDesiredConfiguration.version)).where(NasDesiredConfiguration.nas_id == nas.id)) or 0) + 1
    session.query(NasDesiredConfiguration).filter(NasDesiredConfiguration.nas_id == nas.id, NasDesiredConfiguration.status == "active").update({"status": "superseded"})
    desired = NasDesiredConfiguration(nas_id=nas.id, version=version, configuration=configuration)
    session.add(desired)
    request_id = record_audit(session, tenant_id, "nas.desired_configuration_created", str(desired.id), {"version": version, "configuration_hash": configuration_hash(configuration)})
    session.commit()
    return {**safe_desired(desired), "correlation_id": request_id}

@app.get("/api/nas/{nas_id}/desired-configuration", dependencies=[Depends(internal_service_auth)])
def get_desired_configuration(nas_id: UUID, tenant_id: UUID, session: Session = Depends(db)):
    tenant_item(session, Nas, nas_id, tenant_id, "NAS")
    desired = session.scalar(select(NasDesiredConfiguration).where(NasDesiredConfiguration.nas_id == nas_id, NasDesiredConfiguration.status == "active").order_by(NasDesiredConfiguration.version.desc()))
    if not desired: raise HTTPException(404, "desired configuration not found")
    return safe_desired(desired)

@app.post("/api/nas/{nas_id}/plan", dependencies=[Depends(internal_service_auth)])
def create_nas_plan(nas_id: UUID, tenant_id: UUID, session: Session = Depends(db)):
    nas = tenant_item(session, Nas, nas_id, tenant_id, "NAS")
    desired = session.scalar(select(NasDesiredConfiguration).where(NasDesiredConfiguration.nas_id == nas.id, NasDesiredConfiguration.status == "active").order_by(NasDesiredConfiguration.version.desc()))
    if not desired: raise HTTPException(422, "desired configuration is required before planning")
    assignments = list(session.scalars(select(NasRadiusAssignment).where(NasRadiusAssignment.nas_id == nas.id)))
    snapshot = session.scalar(select(NasSnapshot).where(NasSnapshot.nas_id == nas.id).order_by(NasSnapshot.version.desc()))
    managed_addresses = {str(item.remote_object_id) for item in session.scalars(select(NasRemoteObject).where(NasRemoteObject.nas_id == nas.id, NasRemoteObject.object_type == "radius_entry"))}
    tenant = session.get(Tenant, tenant_id)
    changes, risk, validation = build_plan(desired.configuration, assignments, snapshot.sanitized_configuration if snapshot else None, managed_addresses, nas.capabilities, tenant.policy if tenant else None)
    plan = NasChangePlan(nas_id=nas.id, desired_configuration_id=desired.id, current_snapshot_id=snapshot.id if snapshot else None, planned_changes=changes, risk=risk, validation=validation, requires_approval=risk == "critical", expires_at=datetime.now(timezone.utc) + timedelta(minutes=30))
    session.add(plan)
    request_id = record_audit(session, tenant_id, "nas.configuration_plan_created", str(plan.id), {"risk": risk, "change_count": len(changes), "valid": validation["valid"]})
    outbox(session, "nas.configuration.plan_created.v1", tenant_id, request_id, {"nas_id": str(nas.id), "plan_id": str(plan.id), "risk": risk})
    session.commit()
    return {**safe_plan(plan), "correlation_id": request_id}

@app.get("/api/nas/{nas_id}/plans/{plan_id}", dependencies=[Depends(internal_service_auth)])
def get_nas_plan(nas_id: UUID, plan_id: UUID, tenant_id: UUID, session: Session = Depends(db)):
    tenant_item(session, Nas, nas_id, tenant_id, "NAS")
    plan = session.scalar(select(NasChangePlan).where(NasChangePlan.id == plan_id, NasChangePlan.nas_id == nas_id))
    if not plan: raise HTTPException(404, "NAS plan not found")
    return safe_plan(plan)

@app.post("/api/nas/{nas_id}/plans/{plan_id}/approve", dependencies=[Depends(internal_service_auth)])
def approve_nas_plan(nas_id: UUID, plan_id: UUID, tenant_id: UUID, session: Session = Depends(db)):
    tenant_item(session, Nas, nas_id, tenant_id, "NAS")
    plan = session.scalar(select(NasChangePlan).where(NasChangePlan.id == plan_id, NasChangePlan.nas_id == nas_id))
    if not plan or not plan.validation.get("valid"): raise HTTPException(422, "valid NAS plan not found")
    if plan.requires_approval and plan.status not in {"DRAFT", "PENDING"}: raise HTTPException(409, "NAS plan cannot be approved in its current state")
    plan_expiry = plan.expires_at.replace(tzinfo=timezone.utc) if plan.expires_at.tzinfo is None else plan.expires_at
    if plan_expiry < datetime.now(timezone.utc): raise HTTPException(422, "NAS plan has expired")
    plan.status, plan.approved_by = "APPROVED", request.state.aaa_principal.get("subject", "internal-radius")
    request_id = record_audit(session, tenant_id, "nas.configuration_plan_approved", str(plan.id), {})
    session.commit(); return {**safe_plan(plan), "correlation_id": request_id}

@app.post("/api/nas/{nas_id}/plans/{plan_id}/apply", dependencies=[Depends(internal_service_auth)])
def apply_nas_plan(nas_id: UUID, plan_id: UUID, tenant_id: UUID, payload: NasPlanApplyIn, sync: bool = False, session: Session = Depends(db)):
    nas = tenant_item(session, Nas, nas_id, tenant_id, "NAS")
    plan = session.scalar(select(NasChangePlan).where(NasChangePlan.id == plan_id, NasChangePlan.nas_id == nas.id))
    if not plan or not plan.validation.get("valid"): raise HTTPException(422, "valid NAS plan not found")
    plan_expiry = plan.expires_at.replace(tzinfo=timezone.utc) if plan.expires_at.tzinfo is None else plan.expires_at
    if plan_expiry < datetime.now(timezone.utc): raise HTTPException(422, "NAS plan has expired")
    if plan.requires_approval and plan.status != "APPROVED": raise HTTPException(409, "NAS plan requires approval")
    plan.status = "QUEUED"
    try: nas.lifecycle_status = lifecycle_transition(nas.lifecycle_status, "CONFIGURING")
    except ValueError: pass
    request_id = record_audit(session, tenant_id, "nas.configuration_apply_requested", str(plan.id), {"plan_id": str(plan.id)})
    session.commit()
    return enqueue_or_run(session, nas.id, "configuration_apply", payload.idempotency_key, sync=sync, safe_result={"plan_id": str(plan.id), "change_count": len(plan.planned_changes), "correlation_id": request_id})

@app.get("/api/nas/{nas_id}/jobs", dependencies=[Depends(internal_service_auth)])
def list_nas_jobs(nas_id: UUID, tenant_id: UUID, status: str | None = None, job_type: str | None = None, limit: int = 50, offset: int = 0, session: Session = Depends(db)):
    tenant_item(session, Nas, nas_id, tenant_id, "NAS")
    statement = select(NasJob).where(NasJob.nas_id == nas_id)
    if status: statement = statement.where(NasJob.status == status.upper())
    if job_type: statement = statement.where(NasJob.job_type == job_type)
    return [{"id": str(item.id), "job_type": item.job_type, "status": item.status, "attempts": item.attempts, "maximum_attempts": item.maximum_attempts, "correlation_id": item.correlation_id, "safe_result": item.safe_result, "created_at": item.created_at, "updated_at": item.updated_at} for item in session.scalars(statement.order_by(NasJob.created_at.desc()).offset(max(offset, 0)).limit(bounded(limit)))]

@app.get("/api/nas/{nas_id}/jobs/{job_id}", dependencies=[Depends(internal_service_auth)])
def get_nas_job(nas_id: UUID, job_id: UUID, tenant_id: UUID, session: Session = Depends(db)):
    tenant_item(session, Nas, nas_id, tenant_id, "NAS")
    job = session.scalar(select(NasJob).where(NasJob.id == job_id, NasJob.nas_id == nas_id))
    if not job: raise HTTPException(404, "NAS job not found")
    return {"id": str(job.id), "job_type": job.job_type, "status": job.status, "idempotency_key": job.idempotency_key, "correlation_id": job.correlation_id, "attempts": job.attempts, "maximum_attempts": job.maximum_attempts, "safe_result": job.safe_result, "created_at": job.created_at, "updated_at": job.updated_at}

@app.post("/api/nas/{nas_id}/jobs/{job_id}/cancel", dependencies=[Depends(internal_service_auth)])
def cancel_nas_job(nas_id: UUID, job_id: UUID, tenant_id: UUID, session: Session = Depends(db)):
    tenant_item(session, Nas, nas_id, tenant_id, "NAS")
    job = session.scalar(select(NasJob).where(NasJob.id == job_id, NasJob.nas_id == nas_id))
    if not job: raise HTTPException(404, "NAS job not found")
    if job.status not in {"PENDING", "QUEUED"}: raise HTTPException(409, "only queued jobs can be cancelled")
    job.status = "CANCELLED"
    request_id = record_audit(session, tenant_id, "nas.job_cancelled", str(job.id), {"job_type": job.job_type})
    session.commit(); return {"id": str(job.id), "status": "CANCELLED", "correlation_id": request_id}

@app.post("/api/nas/{nas_id}/rollback", dependencies=[Depends(internal_service_auth)])
def rollback_nas(nas_id: UUID, tenant_id: UUID, payload: NasRollbackIn, sync: bool = False, session: Session = Depends(db)):
    item = tenant_item(session, Nas, nas_id, tenant_id, "NAS")
    request_id = record_audit(session, tenant_id, "nas.rollback_requested", str(item.id), {"reason": payload.reason})
    session.commit()
    return enqueue_or_run(session, item.id, "configuration_rollback", payload.idempotency_key, sync=sync, safe_result={"correlation_id": request_id})

@app.post("/api/nas/{nas_id}/verify", dependencies=[Depends(internal_service_auth)])
def verify_nas(nas_id: UUID, tenant_id: UUID, payload: NasVerifyIn, session: Session = Depends(db)):
    item = tenant_item(session, Nas, nas_id, tenant_id, "NAS")
    desired = session.scalar(select(NasDesiredConfiguration).where(NasDesiredConfiguration.nas_id == item.id, NasDesiredConfiguration.status == "active").order_by(NasDesiredConfiguration.version.desc()))
    if not desired: raise HTTPException(422, "desired configuration is required before verification")
    assignments = list(session.scalars(select(NasRadiusAssignment).where(NasRadiusAssignment.nas_id == item.id)))
    adapter = build_adapter(item, active_credential(session, item.id))
    tenant = session.get(Tenant, tenant_id)
    result = verify_nas_configuration(session, item, adapter, desired.configuration, assignments, tenant.policy if tenant else None)
    if result.get("matched"):
        item.configuration_status = "VERIFIED"
        item.last_verified_at = datetime.now(timezone.utc)
    request_id = record_audit(session, tenant_id, "nas.verified", str(item.id), {"matched": result.get("matched"), "difference_count": len(result.get("differences", []))})
    session.commit()
    return {**result, "nas_id": str(item.id), "correlation_id": request_id}

@app.post("/api/nas/{nas_id}/detect-drift", dependencies=[Depends(internal_service_auth)])
def detect_nas_drift(nas_id: UUID, tenant_id: UUID, session: Session = Depends(db)):
    item = tenant_item(session, Nas, nas_id, tenant_id, "NAS")
    desired = session.scalar(select(NasDesiredConfiguration).where(NasDesiredConfiguration.nas_id == item.id, NasDesiredConfiguration.status == "active").order_by(NasDesiredConfiguration.version.desc()))
    adapter = build_adapter(item, active_credential(session, item.id))
    current = adapter.get_relevant_service_state()
    managed_addresses = {str(remote.remote_object_id) for remote in session.scalars(select(NasRemoteObject).where(NasRemoteObject.nas_id == item.id, NasRemoteObject.object_type == "radius_entry"))}
    desired_config = dict(desired.configuration) if desired else {}
    from .nas_desired_state import build_desired_assignments
    desired_config["radius_assignments"] = build_desired_assignments(list(session.scalars(select(NasRadiusAssignment).where(NasRadiusAssignment.nas_id == item.id))))
    result = detect_drift(current, desired_config, managed_addresses)
    item.configuration_status = "DRIFTED" if result["classification"] in {"WARNING", "CRITICAL"} else item.configuration_status
    request_id = record_audit(session, tenant_id, "nas.drift_detected", str(item.id), {"classification": result["classification"], "item_count": len(result["items"])})
    outbox(session, "nas.configuration.drift_detected.v1", tenant_id, request_id, {"nas_id": str(item.id), "classification": result["classification"], "items": result["items"]})
    session.commit()
    return {**result, "correlation_id": request_id}

@app.post("/api/nas/{nas_id}/reconcile", dependencies=[Depends(internal_service_auth)])
def reconcile_nas(nas_id: UUID, tenant_id: UUID, payload: NasReconcileIn, sync: bool = False, session: Session = Depends(db)):
    item = tenant_item(session, Nas, nas_id, tenant_id, "NAS")
    tenant = session.get(Tenant, tenant_id)
    policy = tenant.policy if tenant else {}
    if payload.reconcile_external and not policy.get("reconcile_external_enabled", False):
        raise HTTPException(403, "external reconciliation is not enabled by tenant policy")
    request_id = record_audit(session, tenant_id, "nas.reconcile_requested", str(item.id), {"reconcile_external": payload.reconcile_external})
    session.commit()
    return enqueue_or_run(session, item.id, "configuration_apply", payload.idempotency_key, sync=sync, safe_result={"correlation_id": request_id, "reconcile_external": payload.reconcile_external})

@app.get("/api/nas/{nas_id}/health", dependencies=[Depends(internal_service_auth)])
def nas_health(nas_id: UUID, tenant_id: UUID, limit: int = 20, session: Session = Depends(db)):
    item = tenant_item(session, Nas, nas_id, tenant_id, "NAS")
    checks = [{"id": str(check.id), "check_type": check.check_type, "status": check.status, "latency_ms": check.latency_ms, "started_at": check.started_at, "completed_at": check.completed_at, "failure_reason": check.failure_reason} for check in session.scalars(select(NasHealthCheck).where(NasHealthCheck.nas_id == item.id).order_by(NasHealthCheck.started_at.desc()).limit(bounded(limit)))]
    return {"nas_id": str(item.id), "health": item.health, "connection_status": item.connection_status, "lifecycle_status": item.lifecycle_status, "last_connected_at": item.last_connected_at, "last_verified_at": item.last_verified_at, "last_accounting_at": item.last_accounting_at, "checks": checks}

@app.get("/api/nas/{nas_id}/activity", dependencies=[Depends(internal_service_auth)])
def managed_nas_activity(nas_id: UUID, tenant_id: UUID, session: Session = Depends(db)):
    item = tenant_item(session, Nas, nas_id, tenant_id, "NAS")
    active = session.scalar(select(func.count()).select_from(ActiveSession).where(ActiveSession.nas_id == item.id, ActiveSession.status != "STOPPED"))
    return {"nas_id": str(item.id), "last_auth_at": item.last_auth_at, "last_accounting_at": item.last_accounting_at, "last_coa_at": item.last_coa_at, "last_connected_at": item.last_connected_at, "last_verified_at": item.last_verified_at, "active_sessions": active}

@app.get("/api/nas/{nas_id}/audit", dependencies=[Depends(internal_service_auth)])
def nas_audit(nas_id: UUID, tenant_id: UUID, action: str | None = None, limit: int = 50, offset: int = 0, session: Session = Depends(db)):
    item = tenant_item(session, Nas, nas_id, tenant_id, "NAS")
    statement = select(AuditLog).where(AuditLog.tenant_id == tenant_id, AuditLog.target_id == str(item.id))
    if action: statement = statement.where(AuditLog.action == action)
    return [{"id": str(log.id), "action": log.action, "target_type": log.target_type, "target_id": log.target_id, "correlation_id": log.correlation_id, "detail": log.detail, "created_at": log.created_at} for log in session.scalars(statement.order_by(AuditLog.created_at.desc()).offset(max(offset, 0)).limit(bounded(limit)))]

@app.post("/api/aaa/credentials", dependencies=[Depends(internal_service_auth)])
def create_credential(payload: CredentialIn, session: Session = Depends(db)):
    if not session.get(Tenant, payload.tenant_id): raise HTTPException(404, "tenant not found")
    credential = Credential(tenant_id=payload.tenant_id, subscriber_id=payload.subscriber_id, username=payload.username, username_normalized=normalize_username(payload.username), password_hash=bcrypt.hashpw(payload.password.encode(), bcrypt.gensalt()).decode(), allowed_methods=payload.allowed_methods, mac_address=normalize_mac(payload.mac_address) if payload.mac_address else None)
    session.add(credential); request_id = record_audit(session, payload.tenant_id, "credential.created", str(payload.subscriber_id), {"methods": payload.allowed_methods}); session.commit(); return {"id": str(credential.id), "correlation_id": request_id}
@app.patch("/api/aaa/credentials/{credential_id}", dependencies=[Depends(internal_service_auth)])
def update_credential(credential_id: UUID, tenant_id: UUID, payload: CredentialUpdateIn, session: Session = Depends(db)):
    item = tenant_item(session, Credential, credential_id, tenant_id, "credential")
    updates = payload.model_dump(exclude_unset=True)
    if "mac_address" in updates and updates["mac_address"]: updates["mac_address"] = normalize_mac(updates["mac_address"])
    if "expires_at" in updates and updates["expires_at"]: updates["expires_at"] = datetime.fromisoformat(updates["expires_at"].replace("Z", "+00:00"))
    for key, value in updates.items(): setattr(item, key, value)
    request_id = record_audit(session, tenant_id, "credential.updated", str(item.subscriber_id), {"fields": sorted(updates)})
    session.commit(); return {"id": str(item.id), "correlation_id": request_id}
@app.post("/api/aaa/credentials/{credential_id}/revoke", dependencies=[Depends(internal_service_auth)])
def revoke_credential(credential_id: UUID, tenant_id: UUID, session: Session = Depends(db)):
    item = tenant_item(session, Credential, credential_id, tenant_id, "credential"); item.status = "revoked"
    request_id = record_audit(session, tenant_id, "credential.revoked", str(item.subscriber_id), {})
    session.commit(); return {"id": str(item.id), "status": item.status, "correlation_id": request_id}
@app.post("/api/aaa/nas", dependencies=[Depends(internal_service_auth)])
def create_nas(payload: NasIn, session: Session = Depends(db)):
    if not session.get(Tenant, payload.tenant_id): raise HTTPException(404, "tenant not found")
    nas = Nas(**payload.model_dump()); session.add(nas); request_id = record_audit(session, payload.tenant_id, "nas.created", str(nas.id), {"source_ip": nas.source_ip}); session.commit(); return {"id": str(nas.id), "secret_displayed": False, "correlation_id": request_id}
@app.get("/api/aaa/nas", dependencies=[Depends(internal_service_auth)])
def list_nas(tenant_id: UUID, limit: int = 100, offset: int = 0, session: Session = Depends(db)):
    return [{"id": str(n.id), "name": n.name, "source_ip": n.source_ip, "enabled": n.enabled, "health": n.health} for n in session.scalars(select(Nas).where(Nas.tenant_id == tenant_id).order_by(Nas.name).offset(max(offset, 0)).limit(bounded(limit)))]
@app.get("/api/aaa/sessions", dependencies=[Depends(internal_service_auth)])
def list_sessions(tenant_id: UUID, username: str | None = None, framed_ip: str | None = None, status: str | None = None, limit: int = 100, offset: int = 0, session: Session = Depends(db)):
    statement = select(ActiveSession).where(ActiveSession.tenant_id == tenant_id)
    if username: statement = statement.where(ActiveSession.username == normalize_username(username))
    if framed_ip: statement = statement.where(ActiveSession.framed_ip == framed_ip)
    if status: statement = statement.where(ActiveSession.status == status)
    return [{"id": str(item.id), "session_id": item.session_id, "status": item.status, "username": item.username, "framed_ip": item.framed_ip, "input_octets": item.input_octets, "output_octets": item.output_octets} for item in session.scalars(statement.order_by(ActiveSession.started_at.desc()).offset(max(offset, 0)).limit(bounded(limit)))]
@app.get("/api/aaa/sessions/{session_id}", dependencies=[Depends(internal_service_auth)])
def get_session(session_id: UUID, tenant_id: UUID, session: Session = Depends(db)):
    item = tenant_item(session, ActiveSession, session_id, tenant_id, "session")
    return {"id": str(item.id), "session_id": item.session_id, "status": item.status, "username": item.username, "framed_ip": item.framed_ip, "input_octets": item.input_octets, "output_octets": item.output_octets, "started_at": item.started_at, "last_interim_at": item.last_interim_at, "termination_cause": item.termination_cause, "policy_snapshot": item.policy_snapshot}

@app.get("/api/aaa/nas/{nas_id}", dependencies=[Depends(internal_service_auth)])
def get_nas(nas_id: UUID, tenant_id: UUID, session: Session = Depends(db)):
    item = session.scalar(select(Nas).where(Nas.id == nas_id, Nas.tenant_id == tenant_id))
    if not item: raise HTTPException(404, "NAS not found")
    return {"id": str(item.id), "name": item.name, "source_ip": item.source_ip, "nas_identifier": item.nas_identifier, "enabled": item.enabled, "health": item.health, "allowed_services": item.allowed_services}
@app.patch("/api/aaa/nas/{nas_id}", dependencies=[Depends(internal_service_auth)])
def update_nas(nas_id: UUID, tenant_id: UUID, payload: NasUpdateIn, session: Session = Depends(db)):
    item = tenant_item(session, Nas, nas_id, tenant_id, "NAS"); updates = payload.model_dump(exclude_unset=True)
    for key, value in updates.items(): setattr(item, key, value)
    request_id = record_audit(session, tenant_id, "nas.updated", str(item.id), {"fields": sorted(updates)}); session.commit()
    return {"id": str(item.id), "correlation_id": request_id}
@app.delete("/api/aaa/nas/{nas_id}", dependencies=[Depends(internal_service_auth)])
def delete_nas(nas_id: UUID, tenant_id: UUID, session: Session = Depends(db)):
    item = tenant_item(session, Nas, nas_id, tenant_id, "NAS")
    if session.scalar(select(ActiveSession.id).where(ActiveSession.nas_id == item.id, ActiveSession.status != "STOPPED").limit(1)): raise HTTPException(409, "cannot delete NAS with active sessions")
    request_id = record_audit(session, tenant_id, "nas.deleted", str(item.id), {"name": item.name}); session.delete(item); session.commit()
    return {"id": str(nas_id), "deleted": True, "correlation_id": request_id}
@app.post("/api/aaa/nas/{nas_id}/enable", dependencies=[Depends(internal_service_auth)])
def enable_nas(nas_id: UUID, tenant_id: UUID, enabled: bool = True, session: Session = Depends(db)):
    item = session.scalar(select(Nas).where(Nas.id == nas_id, Nas.tenant_id == tenant_id))
    if not item: raise HTTPException(404, "NAS not found")
    item.enabled = enabled; request_id = record_audit(session, tenant_id, "nas.enabled" if enabled else "nas.disabled", str(item.id), {}); session.commit(); return {"id": str(item.id), "enabled": item.enabled, "correlation_id": request_id}
@app.post("/api/aaa/nas/{nas_id}/disable", dependencies=[Depends(internal_service_auth)])
def disable_nas(nas_id: UUID, tenant_id: UUID, session: Session = Depends(db)):
    return enable_nas(nas_id, tenant_id, False, session)
@app.post("/api/aaa/nas/{nas_id}/rotate-secret", dependencies=[Depends(internal_service_auth)])
def rotate_nas_secret(nas_id: UUID, tenant_id: UUID, session: Session = Depends(db)):
    item = session.scalar(select(Nas).where(Nas.id == nas_id, Nas.tenant_id == tenant_id))
    if not item: raise HTTPException(404, "NAS not found")
    secret = new_shared_secret(); item.secret_ciphertext = encrypt_secret(secret); item.secret_version += 1; request_id = record_audit(session, tenant_id, "nas.secret_rotated", str(item.id), {"version": item.secret_version}); session.commit()
    return {"id": str(item.id), "secret": secret, "secret_version": item.secret_version, "display_once": True, "correlation_id": request_id}
@app.get("/api/aaa/nas/{nas_id}/activity", dependencies=[Depends(internal_service_auth)])
def nas_activity(nas_id: UUID, tenant_id: UUID, session: Session = Depends(db)):
    item = tenant_item(session, Nas, nas_id, tenant_id, "NAS")
    active = session.scalar(select(func.count()).select_from(ActiveSession).where(ActiveSession.nas_id == item.id, ActiveSession.status != "STOPPED"))
    return {"nas_id": str(item.id), "last_auth_at": item.last_auth_at, "last_accounting_at": item.last_accounting_at, "last_coa_at": item.last_coa_at, "active_sessions": active}
@app.post("/api/aaa/nas/{nas_id}/test-coa", dependencies=[Depends(internal_service_auth)])
def test_nas_coa(nas_id: UUID, tenant_id: UUID, session_id: UUID, idempotency_key: str, session: Session = Depends(db)):
    nas = tenant_item(session, Nas, nas_id, tenant_id, "NAS")
    active = tenant_item(session, ActiveSession, session_id, tenant_id, "session")
    if active.nas_id != nas.id: raise HTTPException(422, "session does not belong to NAS")
    result = coa_session(active.id, tenant_id, CoAIn(idempotency_key=idempotency_key, attributes={"Acct-Session-Id": active.session_id}), session)
    return {**result, "nas_id": str(nas.id), "test_queued": True}
@app.post("/api/aaa/sessions/{session_id}/disconnect", dependencies=[Depends(internal_service_auth)])
def disconnect(session_id: UUID, tenant_id: UUID, idempotency_key: str, session: Session = Depends(db)):
    active = session.scalar(select(ActiveSession).where(ActiveSession.id == session_id, ActiveSession.tenant_id == tenant_id))
    if not active: raise HTTPException(404, "session not found")
    existing = session.scalar(select(RadiusCommand).where(RadiusCommand.tenant_id == tenant_id, RadiusCommand.idempotency_key == idempotency_key))
    if existing: return {"id": str(existing.id), "status": existing.status, "idempotent": True}
    request_id = correlation(None); command = RadiusCommand(tenant_id=tenant_id, nas_id=active.nas_id, session_id=active.id, subscriber_id=active.subscriber_id, command_type="DISCONNECT", status="QUEUED", idempotency_key=idempotency_key, correlation_id=request_id, attributes={"Acct-Session-Id": active.session_id})
    active.status = "DISCONNECT_REQUESTED"; session.add(command); outbox(session, "aaa.disconnect.requested.v1", tenant_id, request_id, {"command_id": str(command.id), "session_id": str(active.id)}, idempotency_key); session.commit()
    return {"id": str(command.id), "status": command.status}
@app.post("/api/aaa/subscribers/{subscriber_id}/disconnect", dependencies=[Depends(internal_service_auth)])
def disconnect_subscriber(subscriber_id: UUID, tenant_id: UUID, idempotency_key: str, session: Session = Depends(db)):
    sessions = list(session.scalars(select(ActiveSession).where(ActiveSession.tenant_id == tenant_id, ActiveSession.subscriber_id == subscriber_id, ActiveSession.status != "STOPPED").limit(100)))
    if not sessions: raise HTTPException(404, "active subscriber sessions not found")
    result = []
    for index, item in enumerate(sessions): result.append(disconnect(item.id, tenant_id, f"{idempotency_key}:{index}", session))
    return {"subscriber_id": str(subscriber_id), "commands": result}
@app.post("/api/aaa/sessions/{session_id}/coa", dependencies=[Depends(internal_service_auth)])
def coa_session(session_id: UUID, tenant_id: UUID, payload: CoAIn, session: Session = Depends(db)):
    active = session.scalar(select(ActiveSession).where(ActiveSession.id == session_id, ActiveSession.tenant_id == tenant_id))
    if not active: raise HTTPException(404, "session not found")
    existing = session.scalar(select(RadiusCommand).where(RadiusCommand.tenant_id == tenant_id, RadiusCommand.idempotency_key == payload.idempotency_key))
    if existing: return {"id": str(existing.id), "status": existing.status, "idempotent": True}
    request_id = correlation(None)
    command = RadiusCommand(tenant_id=tenant_id, nas_id=active.nas_id, session_id=active.id, subscriber_id=active.subscriber_id, command_type="COA", status="QUEUED", idempotency_key=payload.idempotency_key, correlation_id=request_id, attributes=payload.attributes)
    session.add(command); outbox(session, "aaa.coa.requested.v1", tenant_id, request_id, {"command_id": str(command.id), "session_id": str(active.id)}, payload.idempotency_key); session.commit()
    return {"id": str(command.id), "status": command.status}
@app.post("/api/aaa/subscribers/{subscriber_id}/coa", dependencies=[Depends(internal_service_auth)])
def coa_subscriber(subscriber_id: UUID, tenant_id: UUID, payload: CoAIn, session: Session = Depends(db)):
    sessions = list(session.scalars(select(ActiveSession).where(ActiveSession.tenant_id == tenant_id, ActiveSession.subscriber_id == subscriber_id, ActiveSession.status != "STOPPED").limit(100)))
    if not sessions: raise HTTPException(404, "active subscriber sessions not found")
    result = []
    for index, item in enumerate(sessions): result.append(coa_session(item.id, tenant_id, CoAIn(idempotency_key=f"{payload.idempotency_key}:{index}", attributes=payload.attributes), session))
    return {"subscriber_id": str(subscriber_id), "commands": result}
@app.post("/api/aaa/sessions/reconcile", dependencies=[Depends(internal_service_auth)])
def reconcile_sessions(tenant_id: UUID, payload: SessionReconcileIn, session: Session = Depends(db)):
    nas = tenant_item(session, Nas, payload.nas_id, tenant_id, "NAS")
    plan = reconcile_nas_sessions(session, tenant_id, nas.id, set(payload.active_session_ids))
    request_id = record_audit(session, tenant_id, "sessions.reconciliation_planned", str(nas.id), {"database_only": len(plan["database_only"]), "router_only": len(plan["router_only"])})
    session.commit()
    return {"nas_id": str(nas.id), **plan, "simulation": True, "correlation_id": request_id}
@app.get("/api/aaa/accounting-events", dependencies=[Depends(internal_service_auth)])
def list_accounting(tenant_id: UUID, limit: int = 100, session: Session = Depends(db)):
    limit = min(max(limit, 1), 100)
    return [{"id": str(item.id), "session_id": item.session_id, "event_type": item.event_type, "received_at": item.received_at} for item in session.scalars(select(AccountingEvent).where(AccountingEvent.tenant_id == tenant_id).order_by(AccountingEvent.received_at.desc()).limit(limit))]
@app.post("/api/aaa/accounting-events/{event_id}/replay", dependencies=[Depends(internal_service_auth)])
def replay_accounting(event_id: UUID, tenant_id: UUID, session: Session = Depends(db)):
    event = session.scalar(select(AccountingEvent).where(AccountingEvent.id == event_id, AccountingEvent.tenant_id == tenant_id))
    if not event: raise HTTPException(404, "accounting event not found")
    request_id = correlation(None)
    outbox(session, "aaa.accounting.received.v1", tenant_id, request_id, {"accounting_event_id": str(event.id), "event_type": event.event_type, "replay": True}, f"replay:{event.id}:{request_id}")
    session.commit()
    return {"event_id": str(event.id), "replay_queued": True, "correlation_id": request_id}
@app.post("/api/aaa/ip-pools", dependencies=[Depends(internal_service_auth)])
def create_ip_pool(payload: IpPoolIn, session: Session = Depends(db)):
    if not session.get(Tenant, payload.tenant_id): raise HTTPException(404, "tenant not found")
    try: cidr = validate_pool(payload.cidr, payload.address_family)
    except InvalidPool as error: raise HTTPException(422, str(error)) from error
    item = IpPool(**payload.model_dump(exclude={"cidr"}), cidr=cidr); session.add(item); session.commit()
    return {"id": str(item.id), "cidr": item.cidr}
@app.get("/api/aaa/ip-pools", dependencies=[Depends(internal_service_auth)])
def list_ip_pools(tenant_id: UUID, session: Session = Depends(db)):
    return [{"id": str(item.id), "name": item.name, "cidr": item.cidr, "family": item.address_family, "enabled": item.enabled} for item in session.scalars(select(IpPool).where(IpPool.tenant_id == tenant_id).limit(100))]
@app.get("/api/aaa/ip-pools/{pool_id}/leases", dependencies=[Depends(internal_service_auth)])
def list_ip_leases(pool_id: UUID, tenant_id: UUID, limit: int = 100, offset: int = 0, session: Session = Depends(db)):
    tenant_item(session, IpPool, pool_id, tenant_id, "IP pool")
    return [{"id": str(item.id), "address": item.address, "subscriber_id": str(item.subscriber_id) if item.subscriber_id else None, "reservation": item.reservation, "active_session_id": str(item.active_session_id) if item.active_session_id else None, "released_at": item.released_at} for item in session.scalars(select(IpLease).where(IpLease.tenant_id == tenant_id, IpLease.pool_id == pool_id).order_by(IpLease.address).offset(max(offset, 0)).limit(bounded(limit)))]
@app.post("/api/aaa/ip-pools/{pool_id}/reservations", dependencies=[Depends(internal_service_auth)])
def reserve_ip(pool_id: UUID, tenant_id: UUID, payload: IpReservationIn, session: Session = Depends(db)):
    import ipaddress
    pool = tenant_item(session, IpPool, pool_id, tenant_id, "IP pool")
    try: address = ipaddress.ip_address(payload.address); network = ipaddress.ip_network(pool.cidr)
    except ValueError as error: raise HTTPException(422, "invalid reservation address") from error
    if address not in network or str(address) in pool.excluded: raise HTTPException(422, "address is outside eligible pool range")
    existing = session.scalar(select(IpLease).where(IpLease.tenant_id == tenant_id, IpLease.address == str(address)))
    if existing and existing.subscriber_id != payload.subscriber_id: raise HTTPException(409, "address already assigned")
    item = existing or IpLease(tenant_id=tenant_id, pool_id=pool.id, subscriber_id=payload.subscriber_id, address=str(address), reservation=True)
    item.reservation, item.released_at = True, None; session.add(item)
    request_id = record_audit(session, tenant_id, "ip.reserved", str(item.id), {"pool_id": str(pool.id), "subscriber_id": str(payload.subscriber_id)}); session.commit()
    return {"id": str(item.id), "address": item.address, "reservation": True, "correlation_id": request_id}
@app.post("/api/aaa/ip-leases/{lease_id}/release", dependencies=[Depends(internal_service_auth)])
def release_ip(lease_id: UUID, tenant_id: UUID, session: Session = Depends(db)):
    item = tenant_item(session, IpLease, lease_id, tenant_id, "IP lease")
    if item.reservation: raise HTTPException(409, "remove reservation before releasing its address")
    from .ipam import release
    release(session, item); request_id = record_audit(session, tenant_id, "ip.released", str(item.id), {"address": item.address}); session.commit()
    return {"id": str(item.id), "released": True, "correlation_id": request_id}
@app.post("/api/aaa/radius-servers", dependencies=[Depends(internal_service_auth)])
def create_radius_server(payload: RadiusServerIn, session: Session = Depends(db)):
    item = RadiusServer(**payload.model_dump(exclude={"internal_api_key"}), api_key_hash=hash_api_key(payload.internal_api_key))
    session.add(item); session.commit(); return {"id": str(item.id), "name": item.name, "host": item.host, "api_key_stored": True}
@app.post("/api/aaa/radius-server-groups", dependencies=[Depends(internal_service_auth)])
def create_radius_server_group(payload: RadiusServerGroupIn, session: Session = Depends(db)):
    if payload.tenant_id and not session.get(Tenant, payload.tenant_id): raise HTTPException(404, "tenant not found")
    item = RadiusServerGroup(**payload.model_dump()); session.add(item)
    request_id = record_audit(session, item.tenant_id, "radius_server_group.created", str(item.id), {"name": item.name}); session.commit()
    return {"id": str(item.id), "correlation_id": request_id}
@app.get("/api/aaa/radius-server-groups", dependencies=[Depends(internal_service_auth)])
def list_radius_server_groups(tenant_id: UUID | None = None, limit: int = 100, offset: int = 0, session: Session = Depends(db)):
    statement = select(RadiusServerGroup)
    if tenant_id: statement = statement.where((RadiusServerGroup.tenant_id == tenant_id) | RadiusServerGroup.tenant_id.is_(None))
    return [{"id": str(item.id), "name": item.name, "tenant_id": str(item.tenant_id) if item.tenant_id else None, "region": item.region, "minimum_healthy": item.minimum_healthy, "enabled": item.enabled, "failover_policy": item.failover_policy} for item in session.scalars(statement.order_by(RadiusServerGroup.name).offset(max(offset, 0)).limit(bounded(limit)))]
@app.patch("/api/aaa/radius-server-groups/{group_id}", dependencies=[Depends(internal_service_auth)])
def update_radius_server_group(group_id: UUID, payload: RadiusServerGroupUpdateIn, session: Session = Depends(db)):
    item = session.get(RadiusServerGroup, group_id)
    if not item: raise HTTPException(404, "RADIUS server group not found")
    updates = payload.model_dump(exclude_unset=True)
    for key, value in updates.items(): setattr(item, key, value)
    request_id = record_audit(session, item.tenant_id, "radius_server_group.updated", str(item.id), {"fields": sorted(updates)}); session.commit()
    return {"id": str(item.id), "correlation_id": request_id}
@app.delete("/api/aaa/radius-server-groups/{group_id}", dependencies=[Depends(internal_service_auth)])
def delete_radius_server_group(group_id: UUID, session: Session = Depends(db)):
    item = session.get(RadiusServerGroup, group_id)
    if not item: raise HTTPException(404, "RADIUS server group not found")
    if session.scalar(select(Nas.id).where(Nas.radius_group_id == item.id).limit(1)) or session.scalar(select(RadiusServer.id).where(RadiusServer.group_id == item.id).limit(1)): raise HTTPException(409, "remove assignments before deleting group")
    request_id = record_audit(session, item.tenant_id, "radius_server_group.deleted", str(item.id), {"name": item.name}); session.delete(item); session.commit()
    return {"id": str(group_id), "deleted": True, "correlation_id": request_id}
@app.get("/api/aaa/radius-servers", dependencies=[Depends(internal_service_auth)])
def list_radius_servers(session: Session = Depends(db)):
    return [{"id": str(item.id), "name": item.name, "host": item.host, "enabled": item.enabled, "draining": item.draining, "health": item.health, "last_heartbeat_at": item.last_heartbeat_at} for item in session.scalars(select(RadiusServer).order_by(RadiusServer.name).limit(100))]
@app.get("/api/aaa/radius-servers/{server_id}", dependencies=[Depends(internal_service_auth)])
def get_radius_server(server_id: UUID, session: Session = Depends(db)):
    item = session.get(RadiusServer, server_id)
    if not item: raise HTTPException(404, "RADIUS server not found")
    return {"id": str(item.id), "name": item.name, "host": item.host, "environment": item.environment, "region": item.region, "enabled": item.enabled, "draining": item.draining, "health": item.health, "version_metadata": item.version_metadata}
@app.patch("/api/aaa/radius-servers/{server_id}", dependencies=[Depends(internal_service_auth)])
def update_radius_server(server_id: UUID, payload: RadiusServerUpdateIn, session: Session = Depends(db)):
    item = session.get(RadiusServer, server_id)
    if not item: raise HTTPException(404, "RADIUS server not found")
    updates = payload.model_dump(exclude_unset=True)
    for key, value in updates.items(): setattr(item, key, value)
    request_id = record_audit(session, None, "radius_server.updated", str(item.id), {"fields": sorted(updates)}); session.commit()
    return {"id": str(item.id), "correlation_id": request_id}
@app.delete("/api/aaa/radius-servers/{server_id}", dependencies=[Depends(internal_service_auth)])
def delete_radius_server(server_id: UUID, session: Session = Depends(db)):
    item = session.get(RadiusServer, server_id)
    if not item: raise HTTPException(404, "RADIUS server not found")
    request_id = record_audit(session, None, "radius_server.deleted", str(item.id), {"name": item.name}); session.delete(item); session.commit()
    return {"id": str(server_id), "deleted": True, "correlation_id": request_id}
@app.post("/api/aaa/radius-servers/{server_id}/enable", dependencies=[Depends(internal_service_auth)])
def set_radius_server_enabled(server_id: UUID, enabled: bool = True, session: Session = Depends(db)):
    item = session.get(RadiusServer, server_id)
    if not item: raise HTTPException(404, "RADIUS server not found")
    item.enabled = enabled; request_id = record_audit(session, None, "radius_server.enabled" if enabled else "radius_server.disabled", str(item.id), {}); session.commit(); return {"id": str(item.id), "enabled": item.enabled, "correlation_id": request_id}
@app.post("/api/aaa/radius-servers/{server_id}/disable", dependencies=[Depends(internal_service_auth)])
def disable_radius_server(server_id: UUID, session: Session = Depends(db)):
    return set_radius_server_enabled(server_id, False, session)
@app.post("/api/aaa/radius-servers/{server_id}/heartbeat", dependencies=[Depends(internal_service_auth)])
def heartbeat_radius_server(server_id: UUID, payload: HeartbeatIn, session: Session = Depends(db)):
    from datetime import datetime, timezone
    item = session.get(RadiusServer, server_id)
    if not item: raise HTTPException(404, "RADIUS server not found")
    item.last_heartbeat_at = datetime.now(timezone.utc); item.health = "healthy"; item.version_metadata = payload.version_metadata; session.commit()
    return {"id": str(item.id), "health": item.health, "last_heartbeat_at": item.last_heartbeat_at}
@app.get("/api/aaa/subscribers/{subscriber_id}/effective-policy", dependencies=[Depends(internal_service_auth)])
def effective_policy(subscriber_id: UUID, tenant_id: UUID, session: Session = Depends(db)):
    credential = session.scalar(select(Credential).where(Credential.tenant_id == tenant_id, Credential.subscriber_id == subscriber_id))
    if not credential: raise HTTPException(404, "subscriber credential not found")
    tenant = session.get(Tenant, tenant_id)
    policy = calculate_policy({"tenant": tenant.policy.get("default_policy", {})})
    return {"subscriber_id": str(subscriber_id), "policy": policy.values, "provenance": policy.provenance, "reply_attributes": policy.reply_attributes(), "simulation": True}
@app.post("/api/aaa/subscribers/{subscriber_id}/preview-policy", dependencies=[Depends(internal_service_auth)])
def preview_policy(subscriber_id: UUID, tenant_id: UUID, payload: PolicyPreviewIn, session: Session = Depends(db)):
    credential = session.scalar(select(Credential).where(Credential.tenant_id == tenant_id, Credential.subscriber_id == subscriber_id))
    tenant = session.get(Tenant, tenant_id)
    if not credential or not tenant: raise HTTPException(404, "subscriber credential not found")
    layers = {"tenant": tenant.policy.get("default_policy", {}), "subscriber": payload.overrides}
    if payload.nas_id:
        nas = tenant_item(session, Nas, payload.nas_id, tenant_id, "NAS"); layers["nas"] = nas.capabilities.get("policy", {})
    policy = calculate_policy(layers)
    return {"subscriber_id": str(subscriber_id), "policy": policy.values, "provenance": policy.provenance, "reply_attributes": policy.reply_attributes(), "simulation": True}
@app.post("/api/aaa/subscribers/{subscriber_id}/test-eligibility", dependencies=[Depends(internal_service_auth)])
def test_eligibility(subscriber_id: UUID, tenant_id: UUID, payload: PolicyPreviewIn, session: Session = Depends(db)):
    credential = session.scalar(select(Credential).where(Credential.tenant_id == tenant_id, Credential.subscriber_id == subscriber_id))
    if not credential: raise HTTPException(404, "subscriber credential not found")
    if credential.status != "active": return {"eligible": False, "decision": "REJECT_ACCOUNT_DISABLED", "simulation": True}
    if credential.expires_at and credential.expires_at < datetime.now(credential.expires_at.tzinfo): return {"eligible": False, "decision": "REJECT_ACCOUNT_EXPIRED", "simulation": True}
    if payload.nas_id:
        nas = tenant_item(session, Nas, payload.nas_id, tenant_id, "NAS")
        if not nas.enabled: return {"eligible": False, "decision": "REJECT_NAS_DISABLED", "simulation": True}
    return {"eligible": True, "decision": "ACCEPT", "simulation": True}
@app.post("/api/aaa/subscribers/{subscriber_id}/rotate-credential", dependencies=[Depends(internal_service_auth)])
def rotate_credential(subscriber_id: UUID, tenant_id: UUID, payload: PasswordRotationIn, session: Session = Depends(db)):
    credential = session.scalar(select(Credential).where(Credential.tenant_id == tenant_id, Credential.subscriber_id == subscriber_id))
    if not credential: raise HTTPException(404, "subscriber credential not found")
    credential.password_hash = bcrypt.hashpw(payload.password.encode(), bcrypt.gensalt()).decode(); session.commit()
    return {"subscriber_id": str(subscriber_id), "rotated": True}
@app.post("/api/aaa/subscribers/{subscriber_id}/enable", dependencies=[Depends(internal_service_auth)])
def set_subscriber_enabled(subscriber_id: UUID, tenant_id: UUID, enabled: bool = True, session: Session = Depends(db)):
    credential = session.scalar(select(Credential).where(Credential.tenant_id == tenant_id, Credential.subscriber_id == subscriber_id))
    if not credential: raise HTTPException(404, "subscriber credential not found")
    credential.status = "active" if enabled else "disabled"; session.commit()
    return {"subscriber_id": str(subscriber_id), "enabled": enabled}
@app.get("/api/aaa/usage", dependencies=[Depends(internal_service_auth)])
def list_usage(tenant_id: UUID, session: Session = Depends(db)):
    return [{"subscriber_id": str(item.subscriber_id), "period": item.period, "input_octets": item.input_octets, "output_octets": item.output_octets, "fup_active": item.fup_active} for item in session.scalars(select(UsageProjection).where(UsageProjection.tenant_id == tenant_id).limit(100))]
@app.get("/api/aaa/usage/subscribers/{subscriber_id}", dependencies=[Depends(internal_service_auth)])
def subscriber_usage(subscriber_id: UUID, tenant_id: UUID, session: Session = Depends(db)):
    return [{"period": item.period, "input_octets": item.input_octets, "output_octets": item.output_octets, "fup_active": item.fup_active} for item in session.scalars(select(UsageProjection).where(UsageProjection.tenant_id == tenant_id, UsageProjection.subscriber_id == subscriber_id).limit(100))]
@app.get("/api/aaa/audit", dependencies=[Depends(internal_service_auth)])
def list_audit(tenant_id: UUID, action: str | None = None, limit: int = 100, offset: int = 0, session: Session = Depends(db)):
    statement = select(AuditLog).where(AuditLog.tenant_id == tenant_id)
    if action: statement = statement.where(AuditLog.action == action)
    return [{"id": str(item.id), "action": item.action, "target_type": item.target_type, "target_id": item.target_id, "correlation_id": item.correlation_id, "detail": item.detail, "created_at": item.created_at} for item in session.scalars(statement.order_by(AuditLog.created_at.desc()).offset(max(offset, 0)).limit(bounded(limit)))]
@app.post("/api/aaa/usage/subscribers/{subscriber_id}/reset", dependencies=[Depends(internal_service_auth)])
def reset_subscriber_usage(subscriber_id: UUID, tenant_id: UUID, payload: QuotaResetIn, session: Session = Depends(db)):
    period = payload.period or datetime.now().strftime("%Y-%m")
    usage = session.scalar(select(UsageProjection).where(UsageProjection.tenant_id == tenant_id, UsageProjection.subscriber_id == subscriber_id, UsageProjection.period == period))
    if not usage: raise HTTPException(404, "usage projection not found")
    was_fup = usage.fup_active; usage.input_octets = 0; usage.output_octets = 0; usage.fup_active = False
    request_id = record_audit(session, tenant_id, "usage.reset", str(subscriber_id), {"period": period, "was_fup": was_fup})
    if was_fup:
        tenant = session.get(Tenant, tenant_id); normal = calculate_policy({"tenant": tenant.policy.get("default_policy", {})}).reply_attributes()
        outbox(session, "aaa.fup.cleared.v1", tenant_id, request_id, {"subscriber_id": str(subscriber_id), "period": period}, payload.idempotency_key)
        for live in session.scalars(select(ActiveSession).where(ActiveSession.tenant_id == tenant_id, ActiveSession.subscriber_id == subscriber_id, ActiveSession.status.in_(["STARTING", "ACTIVE"]))):
            key = f"reset:{payload.idempotency_key}:{live.id}"
            if session.scalar(select(RadiusCommand.id).where(RadiusCommand.tenant_id == tenant_id, RadiusCommand.idempotency_key == key).limit(1)): continue
            command = RadiusCommand(tenant_id=tenant_id, nas_id=live.nas_id, session_id=live.id, subscriber_id=subscriber_id, command_type="COA", status="QUEUED", idempotency_key=key, correlation_id=request_id, attributes={"Acct-Session-Id": live.session_id, **normal})
            session.add(command); outbox(session, "aaa.coa.requested.v1", tenant_id, request_id, {"command_id": str(command.id), "session_id": str(live.id), "reason": "fup_cleared"}, key)
    session.commit(); return {"subscriber_id": str(subscriber_id), "period": period, "reset": True, "correlation_id": request_id}
