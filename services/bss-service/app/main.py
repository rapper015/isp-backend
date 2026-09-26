from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from os import getenv
from uuid import UUID
import jwt
from fastapi import Depends, FastAPI, HTTPException, Request, status
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from .database import Base, SessionLocal, engine
from .models import BillingAccount, BillingAccountItem, Invoice, Payment, Plan, PlanNetworkPolicyBinding
from .billing_cycles import refresh_overdue_invoices, run_due_billing
from .revenue.router import router as revenue_router
from .revenue.catalog_router import router as catalog_router
from .invoice_imports import router as invoice_import_router
from .revenue.security import internal_service_auth

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

def portal_principal(request: Request) -> dict:
    header = request.headers.get("Authorization", "")
    secret = getenv("CUSTOMER_PORTAL_JWT_SECRET", getenv("BSS_JWT_SECRET", ""))
    if not header.startswith("Bearer ") or len(secret) < 32:
        raise HTTPException(401, "customer portal authentication required")
    try:
        claims = jwt.decode(header[7:], secret, algorithms=["HS256"], issuer="isp-customer-portal", options={"require": ["customer_id", "tenant_id", "exp", "iat", "iss"]})
    except jwt.PyJWTError as error:
        raise HTTPException(401, "invalid or expired customer portal session") from error
    if claims.get("token_type") != "customer_portal": raise HTTPException(401, "invalid customer portal token")
    return claims

class PlanCreate(BaseModel):
    tenant_id: UUID | None = None
    plan_code: str = Field(min_length=1, max_length=64)
    name: str
    description: str = ""
    monthly_fee: Decimal = Field(gt=0)
    download_rate_kbps: int = Field(gt=0)
    upload_rate_kbps: int = Field(gt=0)
    billing_cycle_days: int = Field(default=30, ge=1, le=366)
class PlanResponse(PlanCreate):
    model_config = ConfigDict(from_attributes=True)
    id: UUID; status: str
    network_policy: dict | None = None


class PlanNetworkPolicyBindingIn(BaseModel):
    tenant_id: UUID
    policy_version_id: UUID

class PlanUpdate(BaseModel):
    description: str | None = None
    name: str | None = Field(default=None, min_length=1, max_length=255)
    monthly_fee: Decimal | None = Field(default=None, gt=0)
    download_rate_kbps: int | None = Field(default=None, gt=0)
    upload_rate_kbps: int | None = Field(default=None, gt=0)
    billing_cycle_days: int | None = Field(default=None, ge=1, le=366)
    status: str | None = Field(default=None, min_length=1, max_length=16)
class InvoiceCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    tenant_id: UUID | None = None
    invoice_number: str | None = None
    customer_id: UUID
    subscriber_id: UUID | None = None
    plan_id: UUID
    subtotal: Decimal | None = Field(default=None, ge=0)
    tax_amount: Decimal = Field(default=Decimal("0"), ge=0)
    amount: Decimal | None = Field(default=None, gt=0)
    due_date: datetime
    billing_period_start: datetime | None = None
    billing_period_end: datetime | None = None
    line_items: list[dict] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_totals(self):
        subtotal = self.subtotal
        if subtotal is None and self.amount is not None:
            subtotal = self.amount - self.tax_amount
        if subtotal is None:
            raise ValueError("subtotal or amount is required")
        total = subtotal + self.tax_amount
        if total <= 0:
            raise ValueError("invoice total must be greater than zero")
        if self.amount is not None and self.amount != total:
            raise ValueError("amount must equal subtotal plus tax_amount")
        self.subtotal = subtotal
        self.amount = total
        if self.billing_period_start and self.billing_period_end and self.billing_period_end < self.billing_period_start:
            raise ValueError("billing period end must not precede its start")
        return self

class InvoiceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    tenant_id: UUID | None
    invoice_number: str
    customer_id: UUID
    billing_account_id: UUID | None
    subscriber_id: UUID | None
    plan_id: UUID
    subtotal: Decimal
    tax_amount: Decimal
    amount: Decimal
    balance_due: Decimal
    status: str
    due_date: datetime
    billing_period_start: datetime | None
    billing_period_end: datetime | None
    line_items: list[dict]
    created_at: datetime
    updated_at: datetime | None

class InvoiceUpdate(BaseModel):
    status: str

class BillingScheduleCreate(BaseModel):
    tenant_id: UUID
    customer_id: UUID
    subscriber_id: UUID | None = None
    plan_id: UUID
    account_code: str | None = None
    currency: str = Field(default="INR", min_length=3, max_length=3)
    cycle_days: int | None = Field(default=None, ge=1, le=366)
    due_days: int = Field(default=7, ge=0, le=90)
    tax_percent: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    custom_amount: Decimal | None = Field(default=None, gt=0)
    next_invoice_at: datetime | None = None
    auto_invoice: bool = True

class BillingPlanUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    plan_id: UUID = Field(validation_alias=AliasChoices("plan_id", "planId"))

class BillingItemCreate(BaseModel):
    subscriber_id: UUID
    plan_id: UUID
    description: str = Field(default="Internet service", max_length=255)
    custom_amount: Decimal | None = Field(default=None, gt=0)
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
        "tenant_id": plan.tenant_id,
        "name": plan.name,
        "description": plan.description,
        "monthly_fee": plan.monthly_fee,
        "download_rate_kbps": plan.download_rate_kbps,
        "upload_rate_kbps": plan.upload_rate_kbps,
        "billing_cycle_days": plan.billing_cycle_days,
        "status": plan.status,
        "network_policy": _network_policy_response(binding),
    }

def _schedule_response(item: BillingAccount, db: Session) -> dict:
    outstanding = db.scalar(select(func.coalesce(func.sum(Invoice.balance_due), 0)).where(
        Invoice.tenant_id == item.tenant_id, Invoice.customer_id == item.customer_id,
    ))
    return {
        "id": str(item.id), "tenant_id": str(item.tenant_id), "customer_id": str(item.customer_id),
        "account_number": item.account_number, "account_code": item.account_number,
        "currency": item.currency, "cycle_day": item.next_invoice_at.day,
        "cycle_days": item.cycle_days, "due_days": item.due_days, "tax_percent": str(item.tax_percent),
        "last_invoice_at": item.last_invoice_at, "next_invoice_at": item.next_invoice_at,
        "outstanding_balance": str(outstanding), "status": item.status, "auto_invoice": item.auto_invoice,
        "items": [_billing_item_response(value) for value in db.scalars(select(BillingAccountItem).where(BillingAccountItem.billing_account_id == item.id).order_by(BillingAccountItem.created_at))],
    }


def _billing_item_response(item: BillingAccountItem) -> dict:
    return {"id": str(item.id), "billing_account_id": str(item.billing_account_id), "customer_id": str(item.customer_id),
            "subscriber_id": str(item.subscriber_id), "plan_id": str(item.plan_id), "description": item.description,
            "custom_amount": str(item.custom_amount) if item.custom_amount is not None else None, "status": item.status,
            "effective_from": item.effective_from, "effective_until": item.effective_until}


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


@app.post('/plans', response_model=PlanResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(internal_service_auth)])
def create_plan(payload: PlanCreate, tenant_id: UUID | None = None, db: Session = Depends(db_session)):
    values = payload.model_dump()
    values["tenant_id"] = payload.tenant_id or tenant_id
    plan = Plan(**values); db.add(plan)
    try: db.commit()
    except Exception as exc: db.rollback(); raise HTTPException(409, 'plan_code already exists') from exc
    db.refresh(plan); return _plan_response(plan, db)
@app.get('/plans', response_model=list[PlanResponse], dependencies=[Depends(internal_service_auth)])
def list_plans(tenant_id: UUID | None = None, db: Session = Depends(db_session)):
    statement = select(Plan)
    if tenant_id is not None:
        statement = statement.where((Plan.tenant_id == tenant_id) | (Plan.tenant_id.is_(None)))
    return [_plan_response(plan, db) for plan in db.scalars(statement.order_by(Plan.created_at.desc()))]
@app.get('/plans/{plan_id}', response_model=PlanResponse, dependencies=[Depends(internal_service_auth)])
def get_plan(plan_id: UUID, db: Session = Depends(db_session)):
    plan = db.get(Plan, plan_id)
    if plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, 'plan not found')
    return _plan_response(plan, db)
@app.patch('/plans/{plan_id}', response_model=PlanResponse, dependencies=[Depends(internal_service_auth)])
def update_plan(plan_id: UUID, payload: PlanUpdate, db: Session = Depends(db_session)):
    plan = db.get(Plan, plan_id)
    if plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, 'plan not found')
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(plan, field, value)
    db.commit()
    db.refresh(plan)
    return _plan_response(plan, db)


@app.get('/plans/{plan_id}/network-policy', dependencies=[Depends(internal_service_auth)])
def get_plan_network_policy(plan_id: UUID, db: Session = Depends(db_session)):
    if db.get(Plan, plan_id) is None:
        raise HTTPException(404, 'plan not found')
    binding = db.scalar(select(PlanNetworkPolicyBinding).where(PlanNetworkPolicyBinding.plan_id == plan_id))
    if binding is None:
        raise HTTPException(404, 'no network policy is attached to this plan')
    return _network_policy_response(binding)


@app.put('/plans/{plan_id}/network-policy', dependencies=[Depends(internal_service_auth)])
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
@app.post('/invoices', response_model=InvoiceResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(internal_service_auth)])
def create_invoice(payload: InvoiceCreate, tenant_id: UUID | None = None, db: Session = Depends(db_session)):
    effective_tenant = payload.tenant_id or tenant_id
    plan = db.get(Plan, payload.plan_id)
    if plan is None: raise HTTPException(404, 'plan not found')
    if effective_tenant and plan.tenant_id and effective_tenant != plan.tenant_id:
        raise HTTPException(422, 'plan belongs to a different tenant')
    values = payload.model_dump()
    values["tenant_id"] = effective_tenant
    values["invoice_number"] = payload.invoice_number or f"INV-{datetime.now(timezone.utc):%Y%m%d%H%M%S%f}"
    values["line_items"] = payload.line_items or [{"description": plan.name, "quantity": 1, "unit_price": str(payload.subtotal), "total": str(payload.subtotal)}]
    if effective_tenant and payload.billing_period_end:
        schedule = db.scalar(select(BillingAccount).where(
            BillingAccount.tenant_id == effective_tenant,
            BillingAccount.customer_id == payload.customer_id,
            BillingAccount.status == "active",
        ))
        if schedule is None:
            schedule = BillingAccount(
                tenant_id=effective_tenant, customer_id=payload.customer_id,
                account_number=f"BA-{str(payload.customer_id).replace('-', '')[-12:].upper()}",
                cycle_days=plan.billing_cycle_days, next_invoice_at=payload.billing_period_end + timedelta(seconds=1),
            )
            db.add(schedule); db.flush()
        if payload.subscriber_id and not db.scalar(select(BillingAccountItem.id).where(BillingAccountItem.billing_account_id == schedule.id, BillingAccountItem.subscriber_id == payload.subscriber_id)):
            db.add(BillingAccountItem(billing_account_id=schedule.id, tenant_id=effective_tenant, customer_id=payload.customer_id, subscriber_id=payload.subscriber_id, plan_id=payload.plan_id, description=plan.name))
        values["billing_account_id"] = schedule.id
    invoice = Invoice(**values, balance_due=payload.amount)
    db.add(invoice)
    try: db.commit()
    except Exception as exc: db.rollback(); raise HTTPException(409, 'invoice number already exists') from exc
    db.refresh(invoice); return invoice

@app.post('/billing/accounts', status_code=status.HTTP_201_CREATED, dependencies=[Depends(internal_service_auth)])
def create_billing_schedule(payload: BillingScheduleCreate, db: Session = Depends(db_session)):
    plan = db.get(Plan, payload.plan_id)
    if plan is None: raise HTTPException(404, 'plan not found')
    if plan.tenant_id and plan.tenant_id != payload.tenant_id: raise HTTPException(422, 'plan belongs to a different tenant')
    existing = db.scalar(select(BillingAccount).where(BillingAccount.tenant_id == payload.tenant_id, BillingAccount.customer_id == payload.customer_id))
    if existing:
        if payload.subscriber_id:
            current = db.scalar(select(BillingAccountItem).where(BillingAccountItem.billing_account_id == existing.id, BillingAccountItem.subscriber_id == payload.subscriber_id))
            if current:
                current.plan_id = payload.plan_id; current.custom_amount = payload.custom_amount; current.status = "active"
            else:
                db.add(BillingAccountItem(billing_account_id=existing.id, tenant_id=payload.tenant_id, customer_id=payload.customer_id, subscriber_id=payload.subscriber_id, plan_id=payload.plan_id, description=plan.name, custom_amount=payload.custom_amount))
            db.commit()
        return _schedule_response(existing, db)
    account_code = payload.account_code or f"BA-{str(payload.customer_id).replace('-', '')[-12:].upper()}"
    schedule = BillingAccount(
        tenant_id=payload.tenant_id, customer_id=payload.customer_id,
        account_number=account_code, currency=payload.currency.upper(),
        cycle_days=payload.cycle_days or plan.billing_cycle_days, due_days=payload.due_days,
        tax_percent=payload.tax_percent,
        next_invoice_at=payload.next_invoice_at or datetime.now(timezone.utc), auto_invoice=payload.auto_invoice,
    )
    db.add(schedule); db.flush()
    if payload.subscriber_id:
        db.add(BillingAccountItem(billing_account_id=schedule.id, tenant_id=payload.tenant_id, customer_id=payload.customer_id, subscriber_id=payload.subscriber_id, plan_id=payload.plan_id, description=plan.name, custom_amount=payload.custom_amount))
    try: db.commit()
    except Exception as exc: db.rollback(); raise HTTPException(409, 'billing account code already exists') from exc
    db.refresh(schedule); return _schedule_response(schedule, db)

@app.get('/billing/accounts', dependencies=[Depends(internal_service_auth)])
def list_billing_schedules(tenant_id: UUID | None = None, db: Session = Depends(db_session)):
    statement = select(BillingAccount)
    if tenant_id is not None: statement = statement.where(BillingAccount.tenant_id == tenant_id)
    return [_schedule_response(item, db) for item in db.scalars(statement.order_by(BillingAccount.created_at.desc()))]

@app.put('/billing/accounts/{account_id}/plan', dependencies=[Depends(internal_service_auth)])
def update_billing_plan(account_id: UUID, payload: BillingPlanUpdate, db: Session = Depends(db_session)):
    schedule = db.get(BillingAccount, account_id)
    plan = db.get(Plan, payload.plan_id)
    if schedule is None: raise HTTPException(404, 'billing account not found')
    if plan is None: raise HTTPException(404, 'plan not found')
    if plan.tenant_id and plan.tenant_id != schedule.tenant_id: raise HTTPException(422, 'plan belongs to a different tenant')
    item = db.scalar(select(BillingAccountItem).where(BillingAccountItem.billing_account_id == account_id, BillingAccountItem.status == "active").order_by(BillingAccountItem.created_at))
    if item is None: raise HTTPException(404, 'billing account has no active connection')
    item.plan_id = plan.id
    db.commit(); db.refresh(schedule); return _schedule_response(schedule, db)

@app.post('/billing/accounts/{account_id}/items', status_code=status.HTTP_201_CREATED, dependencies=[Depends(internal_service_auth)])
def add_billing_item(account_id: UUID, payload: BillingItemCreate, db: Session = Depends(db_session)):
    account = db.get(BillingAccount, account_id)
    plan = db.get(Plan, payload.plan_id)
    if account is None: raise HTTPException(404, 'billing account not found')
    if plan is None: raise HTTPException(404, 'plan not found')
    if plan.tenant_id and plan.tenant_id != account.tenant_id: raise HTTPException(422, 'plan belongs to a different tenant')
    item = db.scalar(select(BillingAccountItem).where(BillingAccountItem.billing_account_id == account.id, BillingAccountItem.subscriber_id == payload.subscriber_id))
    if item is None:
        item = BillingAccountItem(billing_account_id=account.id, tenant_id=account.tenant_id, customer_id=account.customer_id, **payload.model_dump())
        db.add(item)
    else:
        item.plan_id = payload.plan_id; item.description = payload.description; item.custom_amount = payload.custom_amount; item.status = 'active'; item.effective_until = None
    db.commit(); db.refresh(item); return _billing_item_response(item)

@app.delete('/billing/accounts/{account_id}/items/{subscriber_id}', status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(internal_service_auth)])
def remove_billing_item(account_id: UUID, subscriber_id: UUID, db: Session = Depends(db_session)):
    item = db.scalar(select(BillingAccountItem).where(BillingAccountItem.billing_account_id == account_id, BillingAccountItem.subscriber_id == subscriber_id))
    if item is None: raise HTTPException(404, 'billing connection not found')
    item.status = 'inactive'; item.effective_until = datetime.now(timezone.utc); db.commit()
@app.post('/payments', response_model=PaymentResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(internal_service_auth)])
def record_payment(payload: PaymentCreate, db: Session = Depends(db_session)):
    invoice = db.get(Invoice, payload.invoice_id)
    if invoice is None: raise HTTPException(404, 'invoice not found')
    if payload.amount > invoice.balance_due: raise HTTPException(422, 'payment exceeds balance due')
    payment = Payment(**payload.model_dump()); invoice.balance_due -= payload.amount
    invoice.status = 'paid' if invoice.balance_due == 0 else 'partially_paid'
    db.add(payment); db.commit(); db.refresh(payment); return payment
@app.get('/invoices', response_model=list[InvoiceResponse], dependencies=[Depends(internal_service_auth)])
def list_invoices(tenant_id: UUID | None = None, db: Session = Depends(db_session)):
    statement = select(Invoice)
    if tenant_id is not None: statement = statement.where(Invoice.tenant_id == tenant_id)
    return list(db.scalars(statement.order_by(Invoice.created_at.desc())))

@app.post('/invoices/refresh-overdue', dependencies=[Depends(internal_service_auth)])
def refresh_overdue(tenant_id: UUID | None = None, db: Session = Depends(db_session)):
    return {"updated": refresh_overdue_invoices(db, tenant_id=tenant_id)}

@app.post('/invoices/generate-due', dependencies=[Depends(internal_service_auth)])
def generate_due_invoices(tenant_id: UUID | None = None, db: Session = Depends(db_session)):
    return run_due_billing(db, tenant_id=tenant_id)

@app.get('/invoices/{invoice_id}', response_model=InvoiceResponse, dependencies=[Depends(internal_service_auth)])
def get_invoice(invoice_id: UUID, tenant_id: UUID | None = None, db: Session = Depends(db_session)):
    invoice = db.get(Invoice, invoice_id)
    if invoice is None or (tenant_id and invoice.tenant_id != tenant_id): raise HTTPException(404, 'invoice not found')
    return invoice

@app.get('/api/bss/portal/billing')
def customer_portal_billing(request: Request, db: Session = Depends(db_session)):
    principal = portal_principal(request)
    tenant_id, customer_id = UUID(principal['tenant_id']), UUID(principal['customer_id'])
    account = db.scalar(select(BillingAccount).where(BillingAccount.tenant_id == tenant_id, BillingAccount.customer_id == customer_id))
    invoices = list(db.scalars(select(Invoice).where(Invoice.tenant_id == tenant_id, Invoice.customer_id == customer_id).order_by(Invoice.created_at.desc())))
    return {"account": _schedule_response(account, db) if account else None,
            "invoices": [InvoiceResponse.model_validate(invoice).model_dump(mode="json") for invoice in invoices]}

@app.patch('/invoices/{invoice_id}', response_model=InvoiceResponse, dependencies=[Depends(internal_service_auth)])
def update_invoice(invoice_id: UUID, payload: InvoiceUpdate, db: Session = Depends(db_session)):
    invoice = db.get(Invoice, invoice_id)
    if invoice is None: raise HTTPException(404, 'invoice not found')
    target = payload.status.lower()
    transitions = {"draft": {"issued"}, "issued": {"overdue", "void"}, "partially_paid": {"paid", "overdue"}, "overdue": {"partially_paid", "paid", "void"}}
    if target == invoice.status: return invoice
    if target not in transitions.get(invoice.status, set()): raise HTTPException(422, f"cannot transition invoice from {invoice.status} to {target}")
    invoice.status = target; db.commit(); db.refresh(invoice); return invoice

@app.get('/payments', response_model=list[PaymentResponse], dependencies=[Depends(internal_service_auth)])
def list_payments(db: Session = Depends(db_session)): return list(db.scalars(select(Payment)))
