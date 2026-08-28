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
from .models import Invoice, Payment, Plan

@asynccontextmanager
async def lifespan(_app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield
app = FastAPI(title="BSS Service", version="0.1.0", lifespan=lifespan)
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
@app.post('/plans', response_model=PlanResponse, status_code=status.HTTP_201_CREATED)
def create_plan(payload: PlanCreate, db: Session = Depends(db_session)):
    plan = Plan(**payload.model_dump()); db.add(plan)
    try: db.commit()
    except Exception as exc: db.rollback(); raise HTTPException(409, 'plan_code already exists') from exc
    db.refresh(plan); return plan
@app.get('/plans', response_model=list[PlanResponse])
def list_plans(db: Session = Depends(db_session)): return list(db.scalars(select(Plan)))
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
