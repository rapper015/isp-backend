from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal
from os import getenv
from uuid import UUID
from fastapi import Depends, FastAPI, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from .database import Base, SessionLocal, engine
from .models import Invoice, Payment, Plan, PlanNetworkPolicyBinding
from .revenue.router import router as revenue_router
from .revenue.catalog_router import router as catalog_router
from .invoice_imports import router as invoice_import_router

@asynccontextmanager
async def lifespan(_app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield
app = FastAPI(title="BSS Service", version="2.0.0", lifespan=lifespan)
app.include_router(revenue_router)
app.include_router(catalog_router)
app.include_router(invoice_import_router)
def db_session():
    db = SessionLocal()
    try: yield db
    finally: db.close()

class PlanCreate(BaseModel):
    plan_code: str = Field(min_length=1, max_length=64)
    name: str
    monthly_fee: Decimal = Field(gt=0)
    download_rate_kbps: int = Field(gt=0)
    upload_rate_kbps: int = Field(gt=0)
class PlanResponse(PlanCreate):
    model_config = ConfigDict(from_attributes=True)
    id: UUID; status: str
    network_policy: dict | None = None


class PlanNetworkPolicyBindingIn(BaseModel):
    tenant_id: UUID
    policy_version_id: UUID

class PlanUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    monthly_fee: Decimal | None = Field(default=None, gt=0)
    download_rate_kbps: int | None = Field(default=None, gt=0)
    upload_rate_kbps: int | None = Field(default=None, gt=0)
    status: str | None = Field(default=None, min_length=1, max_length=16)
class InvoiceCreate(BaseModel):
    invoice_number: str
    customer_id: UUID
    subscriber_id: UUID | None = None
    plan_id: UUID
    amount: Decimal = Field(gt=0)
    due_date: datetime
class InvoiceResponse(InvoiceCreate):
    model_config = ConfigDict(from_attributes=True)
    id: UUID; balance_due: Decimal; status: str
class PaymentCreate(BaseModel):
    payment_reference: str
    invoice_id: UUID
    amount: Decimal = Field(gt=0)
    method: str
class PaymentResponse(PaymentCreate):
    model_config = ConfigDict(from_attributes=True)
    id: UUID

@app.get('/health')
def health(): return {'status':'ok','service':getenv('SERVICE_NAME','bss-service')}
@app.get('/status')
def service_status(): return {'service':'bss','phase':'billing-api'}
def _network_policy_response(binding: PlanNetworkPolicyBinding | None) -> dict | None:
    if binding is None:
        return None
    return {
        "tenant_id": str(binding.tenant_id),
        "policy_id": str(binding.policy_id),
        "policy_version_id": str(binding.policy_version_id),
        "policy_code": binding.policy_code,
        "policy_version": binding.policy_version,
        "status": binding.status,
        "linked_at": binding.linked_at,
    }


def _plan_response(plan: Plan, db: Session) -> dict:
    binding = db.scalar(select(PlanNetworkPolicyBinding).where(PlanNetworkPolicyBinding.plan_id == plan.id))
    return {
        "id": plan.id,
        "plan_code": plan.plan_code,
        "name": plan.name,
        "monthly_fee": plan.monthly_fee,
        "download_rate_kbps": plan.download_rate_kbps,
        "upload_rate_kbps": plan.upload_rate_kbps,
        "status": plan.status,
        "network_policy": _network_policy_response(binding),
    }


def _active_policy_version(tenant_id: UUID, policy_version_id: UUID) -> dict:
    """Read an AAA policy version using service authentication.

    BSS stores only an AAA policy that is already ACTIVE.  This avoids a plan
    being sold with a draft or disabled network policy.
    """
    import httpx

    base_url = getenv("BSS_AAA_BASE_URL", "http://aaa-service:8000").rstrip("/")
    service_key = getenv("BSS_AAA_INTERNAL_API_KEY", "")
    if not service_key:
        raise HTTPException(503, "BSS to AAA service authentication is not configured")
    try:
        response = httpx.get(
            f"{base_url}/api/aaa/policy-versions/{policy_version_id}",
            params={"tenant_id": str(tenant_id)},
            headers={"X-AAA-Service-Key": service_key},
            timeout=5.0,
        )
    except httpx.HTTPError as error:
        raise HTTPException(503, "AAA policy validation is temporarily unavailable") from error
    if response.status_code == 404:
        raise HTTPException(404, "AAA policy version was not found for this tenant")
    if response.status_code >= 400:
        raise HTTPException(503, "AAA policy validation is temporarily unavailable")
    policy = response.json()
    if policy.get("state") != "ACTIVE":
        raise HTTPException(422, "only an active AAA policy version can be attached to a plan")
    return policy


@app.post('/plans', response_model=PlanResponse, status_code=status.HTTP_201_CREATED)
def create_plan(payload: PlanCreate, db: Session = Depends(db_session)):
    plan = Plan(**payload.model_dump()); db.add(plan)
    try: db.commit()
    except Exception as exc: db.rollback(); raise HTTPException(409, 'plan_code already exists') from exc
    db.refresh(plan); return _plan_response(plan, db)
@app.get('/plans', response_model=list[PlanResponse])
def list_plans(db: Session = Depends(db_session)): return [_plan_response(plan, db) for plan in db.scalars(select(Plan))]
@app.get('/plans/{plan_id}', response_model=PlanResponse)
def get_plan(plan_id: UUID, db: Session = Depends(db_session)):
    plan = db.get(Plan, plan_id)
    if plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, 'plan not found')
    return _plan_response(plan, db)
@app.patch('/plans/{plan_id}', response_model=PlanResponse)
def update_plan(plan_id: UUID, payload: PlanUpdate, db: Session = Depends(db_session)):
    plan = db.get(Plan, plan_id)
    if plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, 'plan not found')
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(plan, field, value)
    db.commit()
    db.refresh(plan)
    return _plan_response(plan, db)


@app.get('/plans/{plan_id}/network-policy')
def get_plan_network_policy(plan_id: UUID, db: Session = Depends(db_session)):
    if db.get(Plan, plan_id) is None:
        raise HTTPException(404, 'plan not found')
    binding = db.scalar(select(PlanNetworkPolicyBinding).where(PlanNetworkPolicyBinding.plan_id == plan_id))
    if binding is None:
        raise HTTPException(404, 'no network policy is attached to this plan')
    return _network_policy_response(binding)


@app.put('/plans/{plan_id}/network-policy')
def attach_plan_network_policy(plan_id: UUID, payload: PlanNetworkPolicyBindingIn, db: Session = Depends(db_session)):
    if db.get(Plan, plan_id) is None:
        raise HTTPException(404, 'plan not found')
    policy = _active_policy_version(payload.tenant_id, payload.policy_version_id)
    binding = db.scalar(select(PlanNetworkPolicyBinding).where(PlanNetworkPolicyBinding.plan_id == plan_id))
    values = {
        "tenant_id": payload.tenant_id,
        "policy_id": UUID(policy["policy_id"]),
        "policy_version_id": payload.policy_version_id,
        "policy_code": policy["policy_code"],
        "policy_version": policy["version"],
        "status": policy["state"],
    }
    if binding is None:
        binding = PlanNetworkPolicyBinding(plan_id=plan_id, **values)
        db.add(binding)
    else:
        for field, value in values.items():
            setattr(binding, field, value)
    db.commit()
    db.refresh(binding)
    return _network_policy_response(binding)
@app.post('/invoices', response_model=InvoiceResponse, status_code=status.HTTP_201_CREATED)
def create_invoice(payload: InvoiceCreate, db: Session = Depends(db_session)):
    if db.get(Plan, payload.plan_id) is None: raise HTTPException(404, 'plan not found')
    invoice = Invoice(**payload.model_dump(), balance_due=payload.amount); db.add(invoice); db.commit(); db.refresh(invoice); return invoice
@app.post('/payments', response_model=PaymentResponse, status_code=status.HTTP_201_CREATED)
def record_payment(payload: PaymentCreate, db: Session = Depends(db_session)):
    invoice = db.get(Invoice, payload.invoice_id)
    if invoice is None: raise HTTPException(404, 'invoice not found')
    if payload.amount > invoice.balance_due: raise HTTPException(422, 'payment exceeds balance due')
    payment = Payment(**payload.model_dump()); invoice.balance_due -= payload.amount
    invoice.status = 'paid' if invoice.balance_due == 0 else 'partially_paid'
    db.add(payment); db.commit(); db.refresh(payment); return payment
@app.get('/invoices', response_model=list[InvoiceResponse])
def list_invoices(db: Session = Depends(db_session)): return list(db.scalars(select(Invoice)))
@app.get('/payments', response_model=list[PaymentResponse])
def list_payments(db: Session = Depends(db_session)): return list(db.scalars(select(Payment)))
