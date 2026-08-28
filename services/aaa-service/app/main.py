"""Private AAA API; no FreeRADIUS process, configuration, or networking is managed here."""
from contextlib import asynccontextmanager
from os import getenv
from uuid import UUID
import bcrypt
from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from .database import Base, SessionLocal, engine
from .models import AccountingEvent, ActiveSession, Credential, IpPool, Nas, RadiusCommand, RadiusServer, Tenant, UsageProjection
from .policy import calculate_policy
from .ipam import InvalidPool, validate_pool
from .radius import AttributeValidationError, normalize_attributes, normalize_mac, normalize_username
from .schemas import AccountingRequest, AuthenticationRequest, AuthorizationRequest, CredentialIn, HeartbeatIn, IpPoolIn, NasIn, PasswordRotationIn, PostAuthRequest, RadiusResponse, RadiusServerIn, TenantIn
from .security import encrypt_secret, hash_api_key, internal_service_auth, new_shared_secret
from .services import accounting, authenticate, authorize, correlation, outbox

@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield

app = FastAPI(title="AAA Service (private)", version="1.0.0", docs_url="/internal/docs", openapi_url="/internal/openapi.json", lifespan=lifespan)
def db():
    session = SessionLocal()
    try: yield session
    finally: session.close()
def decision_response(decision: str, reply: dict, request_id: str) -> RadiusResponse:
    return RadiusResponse(outcome="Access-Accept" if decision == "ACCEPT" else "Access-Reject", decision=decision, reply_attributes=reply, correlation_id=request_id)
def attrs(payload):
    try: return normalize_attributes(payload.attributes)
    except AttributeValidationError as error: raise HTTPException(422, detail=str(error)) from error

@app.get("/health")
def health(): return {"status": "ok", "service": getenv("SERVICE_NAME", "aaa-service")}
@app.get("/status")
def service_status(): return {"service": "aaa", "phase": "radius-integration-api"}
@app.get("/internal/radius/v1/health", dependencies=[Depends(internal_service_auth)])
def radius_health(): return {"status": "ok", "service": "aaa", "freeradius_managed": False}
@app.get("/internal/radius/v1/readiness", dependencies=[Depends(internal_service_auth)])
def readiness(session: Session = Depends(db)):
    session.execute(select(Tenant.id).limit(1)); return {"status": "ready", "database": "ok"}

@app.post("/internal/radius/v1/authenticate", response_model=RadiusResponse, dependencies=[Depends(internal_service_auth)])
def internal_authenticate(payload: AuthenticationRequest, session: Session = Depends(db)):
    attributes, _ = attrs(payload); request_id = correlation(payload.correlation_id)
    decision, reply = authenticate(session, attributes, request_id); session.commit(); return decision_response(decision, reply, request_id)
@app.post("/internal/radius/v1/authorize", response_model=RadiusResponse, dependencies=[Depends(internal_service_auth)])
def internal_authorize(payload: AuthorizationRequest, session: Session = Depends(db)):
    attributes, _ = attrs(payload); request_id = correlation(payload.correlation_id)
    decision, reply = authorize(session, attributes, request_id); session.commit(); return decision_response(decision, reply, request_id)
@app.post("/internal/radius/v1/accounting", response_model=RadiusResponse, dependencies=[Depends(internal_service_auth)])
def internal_accounting(payload: AccountingRequest, session: Session = Depends(db)):
    attributes, diagnostic = attrs(payload); request_id = correlation(payload.correlation_id)
    decision, durable = accounting(session, attributes, diagnostic, request_id, payload.idempotency_key)
    if not durable: session.rollback(); return RadiusResponse(outcome="Access-Reject", decision=decision, correlation_id=request_id)
    session.commit(); return RadiusResponse(outcome="OK", decision=decision, correlation_id=request_id)
@app.post("/internal/radius/v1/post-auth", response_model=RadiusResponse, dependencies=[Depends(internal_service_auth)])
def post_auth(payload: PostAuthRequest): return RadiusResponse(outcome="OK", decision="ACKNOWLEDGED", correlation_id=correlation(payload.correlation_id))

@app.post("/api/aaa/tenants", dependencies=[Depends(internal_service_auth)])
def create_tenant(payload: TenantIn, session: Session = Depends(db)):
    tenant = Tenant(**payload.model_dump()); session.add(tenant); session.commit(); return {"id": str(tenant.id)}
@app.post("/api/aaa/credentials", dependencies=[Depends(internal_service_auth)])
def create_credential(payload: CredentialIn, session: Session = Depends(db)):
    if not session.get(Tenant, payload.tenant_id): raise HTTPException(404, "tenant not found")
    credential = Credential(tenant_id=payload.tenant_id, subscriber_id=payload.subscriber_id, username=payload.username, username_normalized=normalize_username(payload.username), password_hash=bcrypt.hashpw(payload.password.encode(), bcrypt.gensalt()).decode(), allowed_methods=payload.allowed_methods, mac_address=normalize_mac(payload.mac_address) if payload.mac_address else None)
    session.add(credential); session.commit(); return {"id": str(credential.id)}
@app.post("/api/aaa/nas", dependencies=[Depends(internal_service_auth)])
def create_nas(payload: NasIn, session: Session = Depends(db)):
    if not session.get(Tenant, payload.tenant_id): raise HTTPException(404, "tenant not found")
    nas = Nas(**payload.model_dump()); session.add(nas); session.commit(); return {"id": str(nas.id), "secret_displayed": False}
@app.get("/api/aaa/nas", dependencies=[Depends(internal_service_auth)])
def list_nas(tenant_id: UUID, session: Session = Depends(db)):
    return [{"id": str(n.id), "name": n.name, "source_ip": n.source_ip, "enabled": n.enabled, "health": n.health} for n in session.scalars(select(Nas).where(Nas.tenant_id == tenant_id).limit(100))]
@app.get("/api/aaa/sessions", dependencies=[Depends(internal_service_auth)])
def list_sessions(tenant_id: UUID, session: Session = Depends(db)):
    from .models import ActiveSession
    return [{"id": str(item.id), "session_id": item.session_id, "status": item.status, "username": item.username} for item in session.scalars(select(ActiveSession).where(ActiveSession.tenant_id == tenant_id).limit(100))]

@app.get("/api/aaa/nas/{nas_id}", dependencies=[Depends(internal_service_auth)])
def get_nas(nas_id: UUID, tenant_id: UUID, session: Session = Depends(db)):
    item = session.scalar(select(Nas).where(Nas.id == nas_id, Nas.tenant_id == tenant_id))
    if not item: raise HTTPException(404, "NAS not found")
    return {"id": str(item.id), "name": item.name, "source_ip": item.source_ip, "nas_identifier": item.nas_identifier, "enabled": item.enabled, "health": item.health, "allowed_services": item.allowed_services}
@app.post("/api/aaa/nas/{nas_id}/enable", dependencies=[Depends(internal_service_auth)])
def enable_nas(nas_id: UUID, tenant_id: UUID, enabled: bool = True, session: Session = Depends(db)):
    item = session.scalar(select(Nas).where(Nas.id == nas_id, Nas.tenant_id == tenant_id))
    if not item: raise HTTPException(404, "NAS not found")
    item.enabled = enabled; session.commit(); return {"id": str(item.id), "enabled": item.enabled}
@app.post("/api/aaa/nas/{nas_id}/rotate-secret", dependencies=[Depends(internal_service_auth)])
def rotate_nas_secret(nas_id: UUID, tenant_id: UUID, session: Session = Depends(db)):
    item = session.scalar(select(Nas).where(Nas.id == nas_id, Nas.tenant_id == tenant_id))
    if not item: raise HTTPException(404, "NAS not found")
    secret = new_shared_secret(); item.secret_ciphertext = encrypt_secret(secret); item.secret_version += 1; session.commit()
    return {"id": str(item.id), "secret": secret, "secret_version": item.secret_version, "display_once": True}
@app.post("/api/aaa/sessions/{session_id}/disconnect", dependencies=[Depends(internal_service_auth)])
def disconnect(session_id: UUID, tenant_id: UUID, idempotency_key: str, session: Session = Depends(db)):
    active = session.scalar(select(ActiveSession).where(ActiveSession.id == session_id, ActiveSession.tenant_id == tenant_id))
    if not active: raise HTTPException(404, "session not found")
    existing = session.scalar(select(RadiusCommand).where(RadiusCommand.tenant_id == tenant_id, RadiusCommand.idempotency_key == idempotency_key))
    if existing: return {"id": str(existing.id), "status": existing.status, "idempotent": True}
    request_id = correlation(None); command = RadiusCommand(tenant_id=tenant_id, nas_id=active.nas_id, session_id=active.id, subscriber_id=active.subscriber_id, command_type="DISCONNECT", status="QUEUED", idempotency_key=idempotency_key, correlation_id=request_id, attributes={"Acct-Session-Id": active.session_id})
    active.status = "DISCONNECT_REQUESTED"; session.add(command); outbox(session, "aaa.disconnect.requested.v1", tenant_id, request_id, {"command_id": str(command.id), "session_id": str(active.id)}, idempotency_key); session.commit()
    return {"id": str(command.id), "status": command.status}
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
@app.post("/api/aaa/radius-servers", dependencies=[Depends(internal_service_auth)])
def create_radius_server(payload: RadiusServerIn, session: Session = Depends(db)):
    item = RadiusServer(**payload.model_dump(exclude={"internal_api_key"}), api_key_hash=hash_api_key(payload.internal_api_key))
    session.add(item); session.commit(); return {"id": str(item.id), "name": item.name, "host": item.host, "api_key_stored": True}
@app.get("/api/aaa/radius-servers", dependencies=[Depends(internal_service_auth)])
def list_radius_servers(session: Session = Depends(db)):
    return [{"id": str(item.id), "name": item.name, "host": item.host, "enabled": item.enabled, "draining": item.draining, "health": item.health, "last_heartbeat_at": item.last_heartbeat_at} for item in session.scalars(select(RadiusServer).order_by(RadiusServer.name).limit(100))]
@app.get("/api/aaa/radius-servers/{server_id}", dependencies=[Depends(internal_service_auth)])
def get_radius_server(server_id: UUID, session: Session = Depends(db)):
    item = session.get(RadiusServer, server_id)
    if not item: raise HTTPException(404, "RADIUS server not found")
    return {"id": str(item.id), "name": item.name, "host": item.host, "environment": item.environment, "region": item.region, "enabled": item.enabled, "draining": item.draining, "health": item.health, "version_metadata": item.version_metadata}
@app.post("/api/aaa/radius-servers/{server_id}/enable", dependencies=[Depends(internal_service_auth)])
def set_radius_server_enabled(server_id: UUID, enabled: bool = True, session: Session = Depends(db)):
    item = session.get(RadiusServer, server_id)
    if not item: raise HTTPException(404, "RADIUS server not found")
    item.enabled = enabled; session.commit(); return {"id": str(item.id), "enabled": item.enabled}
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
