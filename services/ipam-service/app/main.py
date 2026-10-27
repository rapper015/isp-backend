from contextlib import asynccontextmanager
from os import getenv
from uuid import UUID

import jwt
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import SessionLocal
from .models import IPAddress, IPPool


@asynccontextmanager
async def lifespan(_):
    # Schema changes are Alembic-owned.
    yield


app = FastAPI(title="IPAM Service", version="0.2.0", lifespan=lifespan)


def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def management_claims(request: Request) -> dict:
    header = request.headers.get("Authorization", "")
    secret = getenv("IPAM_JWT_SECRET", "")
    if not header.startswith("Bearer ") or len(secret) < 32:
        raise HTTPException(401, "management authentication failed")
    try:
        return jwt.decode(header[7:], secret, algorithms=["HS256"])
    except jwt.PyJWTError as error:
        raise HTTPException(401, "invalid or expired management token") from error


def authorized_tenant(request: Request, tenant_id: UUID | None) -> UUID | None:
    claims = management_claims(request)
    permissions = set(claims.get("permissions") or [])
    claimed_tenant = claims.get("tenant_id") or claims.get("tenantId")
    if claimed_tenant:
        if tenant_id is not None and str(claimed_tenant) != str(tenant_id):
            raise HTTPException(403, "tenant access denied")
        return UUID(str(claimed_tenant))
    if "*" in permissions:
        return tenant_id
    allowed = {str(value) for value in (claims.get("effective_access") or {}).get("tenant_ids", [])}
    if tenant_id is None and len(allowed) == 1:
        return UUID(next(iter(allowed)))
    if tenant_id is None or str(tenant_id) not in allowed:
        raise HTTPException(403, "tenant access denied")
    return tenant_id


class PoolIn(BaseModel):
    pool_code: str
    network_cidr: str


class PoolOut(PoolIn):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    tenant_id: UUID


class AddressIn(BaseModel):
    pool_id: UUID
    address: str


class AddressOut(AddressIn):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    status: str
    subscriber_id: UUID | None


@app.get("/health")
def health():
    return {"status": "ok", "service": getenv("SERVICE_NAME", "ipam-service")}


@app.get("/status")
def service_status():
    return {"service": "ipam", "phase": "tenant-scoped-allocation-api"}


@app.post("/pools", response_model=PoolOut)
def create_pool(payload: PoolIn, request: Request, tenant_id: UUID = Query(...), session: Session = Depends(db)):
    resolved_tenant = authorized_tenant(request, tenant_id)
    pool = IPPool(tenant_id=resolved_tenant, **payload.model_dump())
    session.add(pool)
    session.commit()
    session.refresh(pool)
    return pool


@app.get("/pools", response_model=list[PoolOut])
def list_pools(request: Request, tenant_id: UUID | None = Query(None), session: Session = Depends(db)):
    resolved_tenant = authorized_tenant(request, tenant_id)
    statement = select(IPPool).where(IPPool.tenant_id.is_not(None))
    if resolved_tenant is not None:
        statement = statement.where(IPPool.tenant_id == resolved_tenant)
    statement = statement.order_by(IPPool.pool_code)
    return list(session.scalars(statement))


@app.post("/addresses", response_model=AddressOut)
def add_address(payload: AddressIn, request: Request, tenant_id: UUID = Query(...), session: Session = Depends(db)):
    authorized_tenant(request, tenant_id)
    pool = session.get(IPPool, payload.pool_id)
    if not pool or pool.tenant_id != tenant_id:
        raise HTTPException(404, "pool not found")
    address = IPAddress(**payload.model_dump())
    session.add(address)
    session.commit()
    session.refresh(address)
    return address


@app.post("/addresses/{address_id}/allocate", response_model=AddressOut)
def allocate(address_id: UUID, request: Request, subscriber_id: UUID, tenant_id: UUID = Query(...), session: Session = Depends(db)):
    authorized_tenant(request, tenant_id)
    address = session.get(IPAddress, address_id)
    pool = session.get(IPPool, address.pool_id) if address else None
    if not address or not pool or pool.tenant_id != tenant_id:
        raise HTTPException(404, "address not found")
    if address.status != "available":
        raise HTTPException(409, "address not available")
    address.status = "allocated"
    address.subscriber_id = subscriber_id
    session.commit()
    session.refresh(address)
    return address


from isp_shared.observability import install_observability

install_observability(app, "ipam-service")
