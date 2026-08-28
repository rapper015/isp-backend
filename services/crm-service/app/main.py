from contextlib import asynccontextmanager
from os import getenv
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import Base, SessionLocal, engine
from .models import Branch, Customer, CustomerLifecycleEvent, Franchise, KycDocument, Lead


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Bootstrap only. Replace this with versioned migrations before production.
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="CRM Service", version="0.1.0", lifespan=lifespan)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class CustomerCreate(BaseModel):
    customer_code: str = Field(min_length=1, max_length=64)
    full_name: str = Field(min_length=1, max_length=255)
    phone: str = Field(min_length=1, max_length=32)
    email: str | None = None
    address: str | None = None
    city: str | None = None


class CustomerResponse(CustomerCreate):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    status: str


class FranchiseCreate(BaseModel):
    franchise_code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)


class FranchiseResponse(FranchiseCreate):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    status: str


class BranchCreate(BaseModel):
    franchise_id: UUID
    branch_code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)


class BranchResponse(BranchCreate):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    status: str


class LeadCreate(BaseModel):
    customer_name: str = Field(min_length=1, max_length=255)
    mobile: str = Field(min_length=1, max_length=32)
    franchise_id: UUID | None = None
    branch_id: UUID | None = None
    email: str | None = None
    lead_source: str | None = None
    notes: str | None = None


class LeadResponse(LeadCreate):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    status: str


class KycDocumentCreate(BaseModel):
    document_type: str = Field(min_length=1, max_length=32)
    document_uri: str = Field(min_length=1)
    document_number: str | None = None


class KycDocumentResponse(KycDocumentCreate):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    customer_id: UUID
    status: str


class LifecycleTransition(BaseModel):
    to_status: str = Field(pattern="^(onboarding|active|inactive|suspended|terminated)$")
    reason: str | None = Field(default=None, max_length=255)


@app.get("/health")
def health():
    return {"status": "ok", "service": getenv("SERVICE_NAME", "crm-service")}


@app.get("/status")
def service_status():
    return {"service": "crm", "phase": "customer-api"}


@app.post("/customers", response_model=CustomerResponse, status_code=status.HTTP_201_CREATED)
def create_customer(payload: CustomerCreate, db: Session = Depends(get_db)):
    customer = Customer(**payload.model_dump())
    db.add(customer)
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="customer_code already exists") from exc
    db.refresh(customer)
    return customer


@app.get("/customers", response_model=list[CustomerResponse])
def list_customers(db: Session = Depends(get_db)):
    return list(db.scalars(select(Customer).order_by(Customer.created_at.desc())))


@app.get("/customers/by-code/{customer_code}", response_model=CustomerResponse)
def get_customer_by_code(customer_code: str, db: Session = Depends(get_db)):
    customer = db.scalar(select(Customer).where(Customer.customer_code == customer_code))
    if customer is None:
        raise HTTPException(status_code=404, detail="customer not found")
    return customer


@app.get("/customers/{customer_id}", response_model=CustomerResponse)
def get_customer(customer_id: UUID, db: Session = Depends(get_db)):
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail="customer not found")
    return customer


@app.post("/franchises", response_model=FranchiseResponse, status_code=status.HTTP_201_CREATED)
def create_franchise(payload: FranchiseCreate, db: Session = Depends(get_db)):
    franchise = Franchise(**payload.model_dump())
    db.add(franchise)
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="franchise_code already exists") from exc
    db.refresh(franchise)
    return franchise


@app.post("/branches", response_model=BranchResponse, status_code=status.HTTP_201_CREATED)
def create_branch(payload: BranchCreate, db: Session = Depends(get_db)):
    if db.get(Franchise, payload.franchise_id) is None:
        raise HTTPException(status_code=404, detail="franchise not found")
    branch = Branch(**payload.model_dump())
    db.add(branch)
    db.commit()
    db.refresh(branch)
    return branch


@app.post("/leads", response_model=LeadResponse, status_code=status.HTTP_201_CREATED)
def create_lead(payload: LeadCreate, db: Session = Depends(get_db)):
    if payload.franchise_id and db.get(Franchise, payload.franchise_id) is None:
        raise HTTPException(status_code=404, detail="franchise not found")
    if payload.branch_id and db.get(Branch, payload.branch_id) is None:
        raise HTTPException(status_code=404, detail="branch not found")
    lead = Lead(**payload.model_dump())
    db.add(lead)
    db.commit()
    db.refresh(lead)
    return lead


@app.get("/leads", response_model=list[LeadResponse])
def list_leads(db: Session = Depends(get_db)):
    return list(db.scalars(select(Lead).order_by(Lead.created_at.desc())))


@app.post("/customers/{customer_id}/kyc-documents", response_model=KycDocumentResponse, status_code=status.HTTP_201_CREATED)
def create_kyc_document(customer_id: UUID, payload: KycDocumentCreate, db: Session = Depends(get_db)):
    if db.get(Customer, customer_id) is None:
        raise HTTPException(status_code=404, detail="customer not found")
    document = KycDocument(customer_id=customer_id, **payload.model_dump())
    db.add(document)
    db.commit()
    db.refresh(document)
    return document


@app.post("/customers/{customer_id}/lifecycle-events", response_model=CustomerResponse)
def transition_customer(customer_id: UUID, payload: LifecycleTransition, db: Session = Depends(get_db)):
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail="customer not found")
    event = CustomerLifecycleEvent(customer_id=customer.id, from_status=customer.status, **payload.model_dump())
    customer.status = payload.to_status
    db.add(event)
    db.commit()
    db.refresh(customer)
    return customer
