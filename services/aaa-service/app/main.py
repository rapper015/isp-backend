"""Private AAA API; no FreeRADIUS process, configuration, or networking is managed here."""
from contextlib import asynccontextmanager
from os import getenv
from uuid import UUID
import bcrypt
from datetime import datetime
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from .database import Base, SessionLocal, engine
from .models import AccountingEvent, ActiveSession, AuditLog, Credential, IpLease, IpPool, Nas, NasCredential, RadiusCommand, RadiusServer, RadiusServerGroup, Tenant, UsageProjection
from .policy import calculate_policy
from .ipam import InvalidPool, validate_pool
from .radius import AttributeValidationError, normalize_attributes, normalize_mac, normalize_username
from .schemas import AccountingRequest, AuthenticationRequest, AuthorizationRequest, CoAIn, CredentialIn, CredentialUpdateIn, HeartbeatIn, IpPoolIn, IpReservationIn, NasDraftIn, NasIn, NasUpdateIn, PasswordRotationIn, PolicyPreviewIn, PostAuthRequest, QuotaResetIn, RadiusResponse, RadiusServerGroupIn, RadiusServerGroupUpdateIn, RadiusServerIn, RadiusServerUpdateIn, SessionReconcileIn, TenantIn
from .security import encrypt_secret, hash_api_key, internal_service_auth, new_shared_secret
from .services import accounting, audit, authenticate, authorize, correlation, outbox
from .reconciliation import reconcile_nas_sessions
from .metrics import increment, snapshot
from .routeros import validate_management_address

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
    try: management_host = validate_management_address(payload.management_host)
    except ValueError as error: raise HTTPException(422, str(error)) from error
    if payload.radius_group_id and not session.scalar(select(RadiusServerGroup).where(RadiusServerGroup.id == payload.radius_group_id, (RadiusServerGroup.tenant_id == payload.tenant_id) | RadiusServerGroup.tenant_id.is_(None))): raise HTTPException(422, "RADIUS group is not available to tenant")
    nas = Nas(tenant_id=payload.tenant_id, name=payload.name, short_name=payload.name[:64], source_ip=payload.radius_source_ip, nas_identifier=payload.nas_identifier, vendor="mikrotik", device_type="routeros", radius_group_id=payload.radius_group_id, allowed_services=payload.services, health="unknown", capabilities={"management_host": management_host, "management_port": payload.management_port, "management_protocol": payload.management_protocol, "lifecycle": "DRAFT"})
    session.add(nas); session.flush()
    session.add(NasCredential(nas_id=nas.id, username_ciphertext=encrypt_secret(payload.routeros_username), secret_ciphertext=encrypt_secret(payload.routeros_password)))
    request_id = record_audit(session, payload.tenant_id, "nas.draft_created", str(nas.id), {"management_host": management_host, "services": payload.services})
    session.commit(); return {"id": str(nas.id), "lifecycle_status": "DRAFT", "correlation_id": request_id}
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
