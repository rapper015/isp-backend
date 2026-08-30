"""CRM service API: Milestone 1 (lead pipeline, KYC, customer profile/service
address, lifecycle + risk) plus the Milestone 0 minimal endpoints preserved for
backward compatibility.

All routes are tenant-scoped, permission-checked, validated, audited and
idempotent where applicable.
"""
from contextlib import asynccontextmanager
from os import getenv
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import Base, SessionLocal, engine
from .models import (AuditLog, Branch, Customer, ExperienceRecovery, ExternalReference,
                     FederationLink, Franchise, KbFeedback, KycCase, KycDocument, Lead,
                     LeadInteraction, FollowUp, LoyaltyScore, ServiceLocation, Tenant,
                     TicketSuggestion, TimelineEntry)
from .schemas import (AddressCreate, BranchIn, CafCreateIn, CafDecisionIn, ContactCreate, ContactUpdate, CustomerCreate, CustomerUpdate, ExternalReferenceIn, FollowUpCompleteIn, FollowUpCreate, FollowUpReschedule, FranchiseIn, InteractionIn, KycCreateIn, KycDecisionIn, KycDocumentIn, LeadAssignIn, LeadConvertIn, LeadCreate, LeadFeasibilityIn, LeadQualifyIn, LeadTransitionIn, LifecycleTransitionIn, MergeIn, RiskOverrideIn, RiskRecordIn, ServiceLocationCreate, TenantIn)
from .security import internal_service_auth
from .services import (caf_service, conversion_service, customer_360, customer_service, duplicate_service, kyc_service, lead_service, lifecycle_service, merge_service, risk_service)
from .services.audit_service import record_audit
from .services.ecosystem_service import (
    EscalationService,
    FederationService,
    KbService,
    LoyaltyService,
    PartnerService,
    RecoveryService,
    RegulatoryService,
    SuggestionService,
    TicketSlaService,
)
from .validation import ValidationError

SERVICE_NAME = getenv("SERVICE_NAME", "crm-service")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if getenv("CRM_AUTO_CREATE_SCHEMA", "").lower() == "true" or str(engine.url).startswith("sqlite"):
        Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="CRM Service", version="1.0.0", docs_url="/internal/docs", openapi_url="/internal/openapi.json", lifespan=lifespan)


def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def bounded(limit: int) -> int:
    return min(max(limit, 1), 100)


def tenant_item(session: Session, model, item_id: UUID, tenant_id: UUID, label: str):
    item = session.scalar(select(model).where(model.id == item_id, model.tenant_id == tenant_id))
    if not item:
        raise HTTPException(404, f"{label} not found")
    return item


def actor_of(request: Request) -> str:
    principal = getattr(request.state, "crm_principal", None)
    return (principal or {}).get("subject", "system")


def _raise(error: Exception) -> HTTPException:
    if isinstance(error, (ValueError, ValidationError)):
        return HTTPException(422, str(error))
    raise error


@app.get("/health")
def health():
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/status")
def service_status():
    return {"service": "crm", "phase": "milestone-1"}


# ---------------------------------------------------------------------------
# Tenants and ownership reference data
# ---------------------------------------------------------------------------

@app.post("/api/crm/tenants", dependencies=[Depends(internal_service_auth)])
def create_tenant(payload: TenantIn, session: Session = Depends(db)):
    tenant = Tenant(**payload.model_dump())
    session.add(tenant)
    session.commit()
    return {"id": str(tenant.id)}


@app.post("/api/crm/franchises", dependencies=[Depends(internal_service_auth)])
def create_franchise(tenant_id: UUID, payload: FranchiseIn, session: Session = Depends(db)):
    tenant_item(session, Tenant, tenant_id, tenant_id, "tenant")
    item = Franchise(tenant_id=tenant_id, **payload.model_dump())
    session.add(item)
    session.commit()
    return {"id": str(item.id), "franchise_code": item.franchise_code}


@app.post("/api/crm/branches", dependencies=[Depends(internal_service_auth)])
def create_branch(tenant_id: UUID, payload: BranchIn, session: Session = Depends(db)):
    tenant_item(session, Tenant, tenant_id, tenant_id, "tenant")
    franchise = tenant_item(session, Franchise, payload.franchise_id, tenant_id, "franchise")
    item = Branch(tenant_id=tenant_id, franchise_id=franchise.id, branch_code=payload.branch_code, name=payload.name)
    session.add(item)
    session.commit()
    return {"id": str(item.id), "branch_code": item.branch_code}


# ---------------------------------------------------------------------------
# Leads
# ---------------------------------------------------------------------------

def safe_lead(item: Lead) -> dict:
    return {
        "id": str(item.id), "lead_number": item.lead_number, "lead_type": item.lead_type,
        "first_name": item.first_name, "last_name": item.last_name, "company_name": item.company_name,
        "primary_mobile": item.primary_mobile, "primary_email": item.primary_email,
        "lead_source": item.lead_source, "priority": item.priority, "stage": item.stage,
        "qualification_score": item.qualification_score, "feasibility_state": item.feasibility_state,
        "assigned_salesperson_id": item.assigned_salesperson_id, "next_followup_at": item.next_followup_at,
        "converted_customer_id": str(item.converted_customer_id) if item.converted_customer_id else None,
        "created_at": item.created_at,
    }


@app.post("/api/crm/leads", dependencies=[Depends(internal_service_auth)])
def create_lead(payload: LeadCreate, tenant_id: UUID, request: Request, session: Session = Depends(db)):
    try:
        lead = lead_service.capture_lead(session, tenant_id, payload.model_dump(), actor_of(request))
    except Exception as error:
        raise _raise(error) from error
    session.commit()
    return {"id": str(lead.id), "lead_number": lead.lead_number, "stage": lead.stage}


@app.get("/api/crm/leads", dependencies=[Depends(internal_service_auth)])
def list_leads(tenant_id: UUID, stage: str | None = None, lead_source: str | None = None, assigned_to: str | None = None, q: str | None = None, sort: str = "-created_at", limit: int = 100, offset: int = 0, session: Session = Depends(db)):
    statement = select(Lead).where(Lead.tenant_id == tenant_id)
    if stage:
        statement = statement.where(Lead.stage == stage.upper())
    if lead_source:
        statement = statement.where(Lead.lead_source == lead_source.upper())
    if assigned_to:
        statement = statement.where(Lead.assigned_salesperson_id == assigned_to)
    if q:
        statement = statement.where((Lead.primary_mobile.ilike(f"%{q}%")) | (Lead.first_name.ilike(f"%{q}%")) | (Lead.last_name.ilike(f"%{q}%")) | (Lead.company_name.ilike(f"%{q}%")) | (Lead.lead_number.ilike(f"%{q}%")))
    descending = sort.startswith("-")
    column = getattr(Lead, sort.lstrip("-"), Lead.created_at)
    order = column.desc() if descending else column.asc()
    return [safe_lead(item) for item in session.scalars(statement.order_by(order, Lead.id).offset(max(offset, 0)).limit(bounded(limit)))]


@app.get("/api/crm/leads/{lead_id}", dependencies=[Depends(internal_service_auth)])
def get_lead(lead_id: UUID, tenant_id: UUID, session: Session = Depends(db)):
    item = tenant_item(session, Lead, lead_id, tenant_id, "lead")
    return safe_lead(item)


@app.post("/api/crm/leads/{lead_id}/assign", dependencies=[Depends(internal_service_auth)])
def assign_lead(lead_id: UUID, tenant_id: UUID, payload: LeadAssignIn, request: Request, session: Session = Depends(db)):
    try:
        lead = lead_service.assign_lead(session, tenant_id, lead_id, payload.assigned_to, payload.method, payload.reason, actor_of(request))
    except Exception as error:
        raise _raise(error) from error
    session.commit()
    return {"id": str(lead.id), "stage": lead.stage}


@app.post("/api/crm/leads/{lead_id}/transition", dependencies=[Depends(internal_service_auth)])
def transition_lead(lead_id: UUID, tenant_id: UUID, payload: LeadTransitionIn, request: Request, session: Session = Depends(db)):
    try:
        lead = lead_service.transition_lead(session, tenant_id, lead_id, payload.to_stage, payload.reason, actor_of(request))
    except Exception as error:
        raise _raise(error) from error
    session.commit()
    return {"id": str(lead.id), "stage": lead.stage}


@app.post("/api/crm/leads/{lead_id}/qualify", dependencies=[Depends(internal_service_auth)])
def qualify_lead(lead_id: UUID, tenant_id: UUID, payload: LeadQualifyIn, request: Request, session: Session = Depends(db)):
    try:
        lead = lead_service.qualify_lead(session, tenant_id, lead_id, payload.score, actor_of(request))
    except Exception as error:
        raise _raise(error) from error
    session.commit()
    return {"id": str(lead.id), "stage": lead.stage, "qualification_score": lead.qualification_score}


@app.post("/api/crm/leads/{lead_id}/request-feasibility", dependencies=[Depends(internal_service_auth)])
def request_feasibility(lead_id: UUID, tenant_id: UUID, request: Request, session: Session = Depends(db)):
    try:
        lead = lead_service.request_feasibility(session, tenant_id, lead_id, actor_of(request))
    except Exception as error:
        raise _raise(error) from error
    session.commit()
    return {"id": str(lead.id), "feasibility_state": lead.feasibility_state}


@app.post("/api/crm/leads/{lead_id}/feasibility-result", dependencies=[Depends(internal_service_auth)])
def feasibility_result(lead_id: UUID, tenant_id: UUID, payload: LeadFeasibilityIn, request: Request, session: Session = Depends(db)):
    try:
        lead = lead_service.record_feasibility_result(session, tenant_id, lead_id, payload.feasible, payload.external_ref, actor_of(request))
    except Exception as error:
        raise _raise(error) from error
    session.commit()
    return {"id": str(lead.id), "feasibility_state": lead.feasibility_state}


@app.post("/api/crm/leads/{lead_id}/interactions", dependencies=[Depends(internal_service_auth)])
def add_interaction(lead_id: UUID, tenant_id: UUID, payload: InteractionIn, request: Request, session: Session = Depends(db)):
    try:
        interaction = lead_service.add_interaction(session, tenant_id, lead_id, payload.model_dump(), actor_of(request))
    except Exception as error:
        raise _raise(error) from error
    session.commit()
    return {"id": str(interaction.id), "channel": interaction.channel}


@app.post("/api/crm/leads/{lead_id}/follow-ups", dependencies=[Depends(internal_service_auth)])
def schedule_followup(lead_id: UUID, tenant_id: UUID, payload: FollowUpCreate, request: Request, session: Session = Depends(db)):
    try:
        followup = lead_service.schedule_followup(session, tenant_id, lead_id, payload.model_dump(), actor_of(request))
    except Exception as error:
        raise _raise(error) from error
    session.commit()
    return {"id": str(followup.id), "status": followup.status, "scheduled_at": followup.scheduled_at}


@app.post("/api/crm/leads/{lead_id}/convert", dependencies=[Depends(internal_service_auth)])
def convert_lead(lead_id: UUID, tenant_id: UUID, payload: LeadConvertIn, request: Request, session: Session = Depends(db)):
    try:
        customer = conversion_service.convert_lead(session, tenant_id, lead_id, payload.model_dump(), actor_of(request), request_bss=payload.request_bss, request_oss=payload.request_oss)
    except Exception as error:
        raise _raise(error) from error
    session.commit()
    return {"customer_id": str(customer.id), "customer_number": customer.customer_number}


@app.post("/api/crm/leads/{lead_id}/reopen", dependencies=[Depends(internal_service_auth)])
def reopen_lead(lead_id: UUID, tenant_id: UUID, request: Request, session: Session = Depends(db)):
    try:
        lead = lead_service.reopen_lead(session, tenant_id, lead_id, actor_of(request))
    except Exception as error:
        raise _raise(error) from error
    session.commit()
    return {"id": str(lead.id), "stage": lead.stage}


@app.get("/api/crm/leads/{lead_id}/history", dependencies=[Depends(internal_service_auth)])
def lead_history(lead_id: UUID, tenant_id: UUID, session: Session = Depends(db)):
    try:
        history = lead_service.lead_history(session, tenant_id, lead_id)
    except Exception as error:
        raise _raise(error) from error
    return [{"from_stage": item.from_stage, "to_stage": item.to_stage, "actor": item.actor, "reason": item.reason, "created_at": item.created_at} for item in history]


# ---------------------------------------------------------------------------
# Interactions and follow-ups
# ---------------------------------------------------------------------------

@app.post("/api/crm/customers/{customer_id}/interactions", dependencies=[Depends(internal_service_auth)])
def customer_interaction(customer_id: UUID, tenant_id: UUID, payload: InteractionIn, request: Request, session: Session = Depends(db)):
    customer_service.get_customer(session, tenant_id, customer_id)
    interaction = LeadInteraction(tenant_id=tenant_id, customer_id=customer_id, actor=actor_of(request), direction=payload.direction, channel=payload.channel, subject=payload.subject, safe_summary=payload.safe_summary, outcome=payload.outcome, next_action=payload.next_action, status="COMPLETED")
    session.add(interaction)
    record_audit(session, tenant_id, actor_of(request), "crm.customer.interaction", "customer", customer_id, {"channel": payload.channel})
    session.commit()
    return {"id": str(interaction.id)}


@app.post("/api/crm/customers/{customer_id}/follow-ups", dependencies=[Depends(internal_service_auth)])
def customer_followup(customer_id: UUID, tenant_id: UUID, payload: FollowUpCreate, request: Request, session: Session = Depends(db)):
    customer_service.get_customer(session, tenant_id, customer_id)
    followup = FollowUp(tenant_id=tenant_id, customer_id=customer_id, subject=payload.subject, safe_summary=payload.safe_summary, scheduled_at=payload.scheduled_at, assigned_to=payload.assigned_to, status="PENDING", created_by=actor_of(request))
    session.add(followup)
    record_audit(session, tenant_id, actor_of(request), "crm.followup.created", "followup", followup.id, {"scheduled_at": payload.scheduled_at.isoformat()})
    session.commit()
    return {"id": str(followup.id), "status": followup.status}


@app.get("/api/crm/follow-ups", dependencies=[Depends(internal_service_auth)])
def list_followups(tenant_id: UUID, status: str | None = None, due: bool = False, overdue: bool = False, limit: int = 100, offset: int = 0, session: Session = Depends(db)):
    statement = select(FollowUp).where(FollowUp.tenant_id == tenant_id)
    if status:
        statement = statement.where(FollowUp.status == status.upper())
    if due:
        from datetime import datetime, timezone
        statement = statement.where(FollowUp.status == "PENDING", FollowUp.scheduled_at <= datetime.now(timezone.utc))
    if overdue:
        statement = statement.where(FollowUp.status == "MISSED")
    return [{"id": str(item.id), "subject": item.subject, "scheduled_at": item.scheduled_at, "status": item.status, "lead_id": str(item.lead_id) if item.lead_id else None, "customer_id": str(item.customer_id) if item.customer_id else None} for item in session.scalars(statement.order_by(FollowUp.scheduled_at).offset(max(offset, 0)).limit(bounded(limit)))]


@app.post("/api/crm/follow-ups/{followup_id}/complete", dependencies=[Depends(internal_service_auth)])
def complete_followup(followup_id: UUID, tenant_id: UUID, payload: FollowUpCompleteIn, request: Request, session: Session = Depends(db)):
    try:
        followup = lead_service.complete_followup(session, tenant_id, followup_id, actor_of(request))
    except Exception as error:
        raise _raise(error) from error
    session.commit()
    return {"id": str(followup.id), "status": followup.status}


@app.post("/api/crm/follow-ups/{followup_id}/reschedule", dependencies=[Depends(internal_service_auth)])
def reschedule_followup(followup_id: UUID, tenant_id: UUID, payload: FollowUpReschedule, request: Request, session: Session = Depends(db)):
    try:
        followup = lead_service.reschedule_followup(session, tenant_id, followup_id, payload.scheduled_at, actor_of(request))
    except Exception as error:
        raise _raise(error) from error
    session.commit()
    return {"id": str(followup.id), "status": followup.status, "scheduled_at": followup.scheduled_at}


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------

def safe_customer(item: Customer) -> dict:
    return {
        "id": str(item.id), "customer_number": item.customer_number, "customer_code": item.customer_code,
        "caf_number": item.caf_number, "customer_type": item.customer_type, "full_name": item.full_name,
        "legal_name": item.legal_name, "first_name": item.first_name, "last_name": item.last_name,
        "company_trading_name": item.company_trading_name, "gstin": item.gstin, "phone": item.phone, "email": item.email,
        "franchise_id": str(item.franchise_id) if item.franchise_id else None,
        "branch_id": str(item.branch_id) if item.branch_id else None,
        "lifecycle_state": item.lifecycle_state, "risk_level": item.risk_level, "status": item.status,
        "activation_date": item.activation_date, "closure_date": item.closure_date,
        "created_at": item.created_at,
    }


@app.post("/api/crm/customers", dependencies=[Depends(internal_service_auth)])
def create_customer(payload: CustomerCreate, tenant_id: UUID, request: Request, session: Session = Depends(db)):
    try:
        customer = customer_service.create_customer(session, tenant_id, payload.model_dump(), actor_of(request))
    except Exception as error:
        raise _raise(error) from error
    session.commit()
    return safe_customer(customer)


@app.get("/api/crm/customers", dependencies=[Depends(internal_service_auth)])
def list_customers(tenant_id: UUID, lifecycle_state: str | None = None, risk_level: str | None = None, q: str | None = None, franchise_id: UUID | None = None, sort: str = "-created_at", limit: int = 100, offset: int = 0, session: Session = Depends(db)):
    statement = select(Customer).where(Customer.tenant_id == tenant_id)
    if lifecycle_state:
        statement = statement.where(Customer.lifecycle_state == lifecycle_state.upper())
    if risk_level:
        statement = statement.where(Customer.risk_level == risk_level.upper())
    if franchise_id:
        statement = statement.where(Customer.franchise_id == franchise_id)
    if q:
        statement = statement.where((Customer.phone.ilike(f"%{q}%")) | (Customer.email.ilike(f"%{q}%")) | (Customer.full_name.ilike(f"%{q}%")) | (Customer.customer_number.ilike(f"%{q}%")) | (Customer.caf_number.ilike(f"%{q}%")))
    descending = sort.startswith("-")
    column = getattr(Customer, sort.lstrip("-"), Customer.created_at)
    order = column.desc() if descending else column.asc()
    return [safe_customer(item) for item in session.scalars(statement.order_by(order, Customer.id).offset(max(offset, 0)).limit(bounded(limit)))]


@app.get("/api/crm/customers/{customer_id}", dependencies=[Depends(internal_service_auth)])
def get_customer(customer_id: UUID, tenant_id: UUID, session: Session = Depends(db)):
    item = tenant_item(session, Customer, customer_id, tenant_id, "customer")
    return safe_customer(item)


@app.patch("/api/crm/customers/{customer_id}", dependencies=[Depends(internal_service_auth)])
def update_customer(customer_id: UUID, tenant_id: UUID, payload: CustomerUpdate, request: Request, session: Session = Depends(db)):
    try:
        customer = customer_service.update_customer(session, tenant_id, customer_id, payload.model_dump(exclude_unset=True), actor_of(request))
    except Exception as error:
        raise _raise(error) from error
    session.commit()
    customer_360.invalidate_customer_360(tenant_id, customer_id)
    return safe_customer(customer)


@app.get("/api/crm/customers/{customer_id}/360", dependencies=[Depends(internal_service_auth)])
def customer_360_view(customer_id: UUID, tenant_id: UUID, session: Session = Depends(db)):
    try:
        return customer_360.customer_360(session, tenant_id, customer_id)
    except Exception as error:
        raise _raise(error) from error


@app.get("/api/crm/customers/{customer_id}/timeline", dependencies=[Depends(internal_service_auth)])
def customer_timeline(customer_id: UUID, tenant_id: UUID, limit: int = 100, offset: int = 0, session: Session = Depends(db)):
    tenant_item(session, Customer, customer_id, tenant_id, "customer")
    return [{"id": str(item.id), "category": item.category, "safe_summary": item.safe_summary, "actor": item.actor, "external_type": item.external_type, "external_id": item.external_id, "occurred_at": item.occurred_at} for item in session.scalars(select(TimelineEntry).where(TimelineEntry.tenant_id == tenant_id, TimelineEntry.customer_id == customer_id).order_by(TimelineEntry.occurred_at.desc()).offset(max(offset, 0)).limit(bounded(limit)))]


@app.post("/api/crm/customers/{customer_id}/transition", dependencies=[Depends(internal_service_auth)])
def transition_customer(customer_id: UUID, tenant_id: UUID, payload: LifecycleTransitionIn, request: Request, session: Session = Depends(db)):
    try:
        customer = lifecycle_service.transition_customer(session, tenant_id, customer_id, payload.to_state, payload.trigger, actor_of(request), payload.reason, payload.related_external_type, payload.related_external_id)
    except Exception as error:
        raise _raise(error) from error
    session.commit()
    customer_360.invalidate_customer_360(tenant_id, customer_id)
    return {"id": str(customer.id), "lifecycle_state": customer.lifecycle_state}


@app.post("/api/crm/customers/{customer_id}/merge-preview", dependencies=[Depends(internal_service_auth)])
def merge_preview(customer_id: UUID, tenant_id: UUID, payload: MergeIn, session: Session = Depends(db)):
    try:
        return merge_service.merge_preview(session, tenant_id, customer_id, payload.duplicate_id)
    except Exception as error:
        raise _raise(error) from error


@app.post("/api/crm/customers/{customer_id}/merge", dependencies=[Depends(internal_service_auth)])
def merge_customer(customer_id: UUID, tenant_id: UUID, payload: MergeIn, request: Request, session: Session = Depends(db)):
    try:
        customer = merge_service.execute_merge(session, tenant_id, customer_id, payload.duplicate_id, actor_of(request))
    except Exception as error:
        raise _raise(error) from error
    session.commit()
    customer_360.invalidate_customer_360(tenant_id, customer_id)
    customer_360.invalidate_customer_360(tenant_id, payload.duplicate_id)
    return {"customer_id": str(customer.id)}


@app.get("/api/crm/customers/{customer_id}/external-references", dependencies=[Depends(internal_service_auth)])
def external_references(customer_id: UUID, tenant_id: UUID, session: Session = Depends(db)):
    tenant_item(session, Customer, customer_id, tenant_id, "customer")
    return [{"id": str(item.id), "service_name": item.service_name, "external_type": item.external_type, "external_id": item.external_id, "external_status": item.external_status, "last_synced_at": item.last_synced_at} for item in session.scalars(select(ExternalReference).where(ExternalReference.tenant_id == tenant_id, ExternalReference.customer_id == customer_id))]


# ---------------------------------------------------------------------------
# Contacts, addresses and service locations
# ---------------------------------------------------------------------------

@app.post("/api/crm/customers/{customer_id}/contacts", dependencies=[Depends(internal_service_auth)])
def add_contact(customer_id: UUID, tenant_id: UUID, payload: ContactCreate, request: Request, session: Session = Depends(db)):
    try:
        contact = customer_service.add_contact(session, tenant_id, customer_id, payload.model_dump(), actor_of(request))
    except Exception as error:
        raise _raise(error) from error
    session.commit()
    return {"id": str(contact.id), "role": contact.role, "is_primary": contact.is_primary}


@app.patch("/api/crm/customers/{customer_id}/contacts/{contact_id}", dependencies=[Depends(internal_service_auth)])
def update_contact(customer_id: UUID, contact_id: UUID, tenant_id: UUID, payload: ContactUpdate, request: Request, session: Session = Depends(db)):
    try:
        contact = customer_service.update_contact(session, tenant_id, customer_id, contact_id, payload.model_dump(exclude_unset=True), actor_of(request))
    except Exception as error:
        raise _raise(error) from error
    session.commit()
    return {"id": str(contact.id)}


@app.post("/api/crm/customers/{customer_id}/contacts/{contact_id}/verify", dependencies=[Depends(internal_service_auth)])
def verify_contact(customer_id: UUID, contact_id: UUID, tenant_id: UUID, request: Request, session: Session = Depends(db)):
    try:
        contact = customer_service.verify_contact(session, tenant_id, customer_id, contact_id, True, actor_of(request))
    except Exception as error:
        raise _raise(error) from error
    session.commit()
    return {"id": str(contact.id), "verification_state": contact.verification_state}


@app.post("/api/crm/customers/{customer_id}/addresses", dependencies=[Depends(internal_service_auth)])
def add_address(customer_id: UUID, tenant_id: UUID, payload: AddressCreate, request: Request, session: Session = Depends(db)):
    try:
        address = customer_service.add_address(session, tenant_id, customer_id, payload.model_dump(), actor_of(request))
    except Exception as error:
        raise _raise(error) from error
    session.commit()
    customer_360.invalidate_customer_360(tenant_id, customer_id)
    return {"id": str(address.id), "address_type": address.address_type, "version": address.version}


@app.patch("/api/crm/customers/{customer_id}/addresses/{address_id}", dependencies=[Depends(internal_service_auth)])
def update_address(customer_id: UUID, address_id: UUID, tenant_id: UUID, payload: AddressCreate, request: Request, session: Session = Depends(db)):
    try:
        address = customer_service.update_address(session, tenant_id, customer_id, address_id, payload.model_dump(exclude_unset=True), actor_of(request))
    except Exception as error:
        raise _raise(error) from error
    session.commit()
    customer_360.invalidate_customer_360(tenant_id, customer_id)
    return {"id": str(address.id), "address_type": address.address_type, "version": address.version}


@app.get("/api/crm/customers/{customer_id}/addresses/history", dependencies=[Depends(internal_service_auth)])
def address_history(customer_id: UUID, tenant_id: UUID, session: Session = Depends(db)):
    try:
        return [{"id": str(item.id), "address_type": item.address_type, "city": item.city, "version": item.version, "valid_from": item.valid_from, "valid_to": item.valid_to} for item in customer_service.address_history(session, tenant_id, customer_id)]
    except Exception as error:
        raise _raise(error) from error


@app.post("/api/crm/customers/{customer_id}/service-locations", dependencies=[Depends(internal_service_auth)])
def create_service_location(customer_id: UUID, tenant_id: UUID, payload: ServiceLocationCreate, request: Request, session: Session = Depends(db)):
    try:
        location = customer_service.create_service_location(session, tenant_id, customer_id, payload.model_dump(), actor_of(request))
    except Exception as error:
        raise _raise(error) from error
    session.commit()
    return {"id": str(location.id), "service_location_number": location.service_location_number}


# ---------------------------------------------------------------------------
# KYC
# ---------------------------------------------------------------------------

@app.post("/api/crm/customers/{customer_id}/kyc", dependencies=[Depends(internal_service_auth)])
def create_kyc(customer_id: UUID, tenant_id: UUID, payload: KycCreateIn, request: Request, session: Session = Depends(db)):
    tenant_item(session, Customer, customer_id, tenant_id, "customer")
    try:
        case = kyc_service.create_kyc_case(session, tenant_id, customer_id, kyc_type=payload.kyc_type, actor=actor_of(request))
    except Exception as error:
        raise _raise(error) from error
    session.commit()
    return {"id": str(case.id), "status": case.status}


@app.get("/api/crm/customers/{customer_id}/kyc", dependencies=[Depends(internal_service_auth)])
def list_kyc(customer_id: UUID, tenant_id: UUID, session: Session = Depends(db)):
    tenant_item(session, Customer, customer_id, tenant_id, "customer")
    return [{"id": str(item.id), "kyc_type": item.kyc_type, "status": item.status, "verification_method": item.verification_method, "verified_at": item.verified_at} for item in session.scalars(select(KycCase).where(KycCase.tenant_id == tenant_id, KycCase.customer_id == customer_id))]


@app.post("/api/crm/kyc/{case_id}/submit", dependencies=[Depends(internal_service_auth)])
def submit_kyc(case_id: UUID, tenant_id: UUID, request: Request, session: Session = Depends(db)):
    try:
        case = kyc_service.submit_kyc(session, tenant_id, case_id, actor_of(request))
    except Exception as error:
        raise _raise(error) from error
    session.commit()
    return {"id": str(case.id), "status": case.status}


@app.post("/api/crm/kyc/{case_id}/request-information", dependencies=[Depends(internal_service_auth)])
def request_kyc_information(case_id: UUID, tenant_id: UUID, request: Request, session: Session = Depends(db)):
    try:
        case = kyc_service.request_more_information(session, tenant_id, case_id, actor_of(request))
    except Exception as error:
        raise _raise(error) from error
    session.commit()
    return {"id": str(case.id), "status": case.status}


@app.post("/api/crm/kyc/{case_id}/verify", dependencies=[Depends(internal_service_auth)])
def verify_kyc(case_id: UUID, tenant_id: UUID, payload: KycDecisionIn, request: Request, session: Session = Depends(db)):
    try:
        case = kyc_service.verify_kyc(session, tenant_id, case_id, payload.method or "manual", actor_of(request))
    except Exception as error:
        raise _raise(error) from error
    session.commit()
    if case.customer_id:
        customer_360.invalidate_customer_360(tenant_id, case.customer_id)
    return {"id": str(case.id), "status": case.status}


@app.post("/api/crm/kyc/{case_id}/reject", dependencies=[Depends(internal_service_auth)])
def reject_kyc(case_id: UUID, tenant_id: UUID, payload: KycDecisionIn, request: Request, session: Session = Depends(db)):
    try:
        case = kyc_service.reject_kyc(session, tenant_id, case_id, payload.reason or "rejected", actor_of(request))
    except Exception as error:
        raise _raise(error) from error
    session.commit()
    return {"id": str(case.id), "status": case.status}


@app.post("/api/crm/kyc/{case_id}/documents", dependencies=[Depends(internal_service_auth)])
def add_kyc_document(case_id: UUID, tenant_id: UUID, payload: KycDocumentIn, request: Request, session: Session = Depends(db)):
    try:
        document = kyc_service.add_kyc_document(session, tenant_id, case_id, payload.document_type, payload.storage_reference, payload.masked_identifier, payload.content_type, payload.size_bytes, payload.checksum)
    except Exception as error:
        raise _raise(error) from error
    session.commit()
    return {"id": str(document.id), "document_type": document.document_type, "masked_identifier": document.masked_identifier}


@app.get("/api/crm/kyc/{case_id}/documents", dependencies=[Depends(internal_service_auth)])
def list_kyc_documents(case_id: UUID, tenant_id: UUID, session: Session = Depends(db)):
    try:
        documents = kyc_service.list_kyc_documents(session, tenant_id, case_id)
    except Exception as error:
        raise _raise(error) from error
    return [{"id": str(item.id), "document_type": item.document_type, "masked_identifier": item.masked_identifier, "verification_state": item.verification_state} for item in documents]


# ---------------------------------------------------------------------------
# CAF
# ---------------------------------------------------------------------------

@app.post("/api/crm/customers/{customer_id}/caf", dependencies=[Depends(internal_service_auth)])
def create_caf(customer_id: UUID, tenant_id: UUID, payload: CafCreateIn, request: Request, session: Session = Depends(db)):
    tenant_item(session, Customer, customer_id, tenant_id, "customer")
    try:
        caf = caf_service.create_caf(session, tenant_id, {**payload.model_dump(), "customer_id": customer_id}, actor_of(request))
    except Exception as error:
        raise _raise(error) from error
    session.commit()
    return {"id": str(caf.id), "caf_number": caf.caf_number, "status": caf.status}


@app.post("/api/crm/caf/{caf_id}/submit", dependencies=[Depends(internal_service_auth)])
def submit_caf(caf_id: UUID, tenant_id: UUID, request: Request, session: Session = Depends(db)):
    try:
        caf = caf_service.submit_caf(session, tenant_id, caf_id, actor_of(request))
    except Exception as error:
        raise _raise(error) from error
    session.commit()
    return {"id": str(caf.id), "status": caf.status}


@app.post("/api/crm/caf/{caf_id}/approve", dependencies=[Depends(internal_service_auth)])
def approve_caf(caf_id: UUID, tenant_id: UUID, payload: CafDecisionIn, request: Request, session: Session = Depends(db)):
    try:
        caf = caf_service.approve_caf(session, tenant_id, caf_id, actor_of(request))
    except Exception as error:
        raise _raise(error) from error
    session.commit()
    return {"id": str(caf.id), "status": caf.status}


@app.post("/api/crm/caf/{caf_id}/reject", dependencies=[Depends(internal_service_auth)])
def reject_caf(caf_id: UUID, tenant_id: UUID, payload: CafDecisionIn, request: Request, session: Session = Depends(db)):
    try:
        caf = caf_service.reject_caf(session, tenant_id, caf_id, payload.reason or "rejected", actor_of(request))
    except Exception as error:
        raise _raise(error) from error
    session.commit()
    return {"id": str(caf.id), "status": caf.status}


# ---------------------------------------------------------------------------
# Risk
# ---------------------------------------------------------------------------

@app.post("/api/crm/customers/{customer_id}/risk", dependencies=[Depends(internal_service_auth)])
def record_risk(customer_id: UUID, tenant_id: UUID, payload: RiskRecordIn, session: Session = Depends(db)):
    tenant_item(session, Customer, customer_id, tenant_id, "customer")
    try:
        risk = risk_service.record_risk(session, tenant_id, customer_id, payload.level, payload.source, payload.reason, payload.source_event_id)
    except Exception as error:
        raise _raise(error) from error
    session.commit()
    customer_360.invalidate_customer_360(tenant_id, customer_id)
    return {"id": str(risk.id), "level": risk.level, "effective_level": risk.effective_level}


@app.post("/api/crm/customers/{customer_id}/risk/override", dependencies=[Depends(internal_service_auth)])
def override_risk(customer_id: UUID, tenant_id: UUID, payload: RiskOverrideIn, request: Request, session: Session = Depends(db)):
    tenant_item(session, Customer, customer_id, tenant_id, "customer")
    try:
        risk = risk_service.override_risk(session, tenant_id, customer_id, payload.level, payload.reason, actor_of(request), payload.expires_in_seconds)
    except Exception as error:
        raise _raise(error) from error
    session.commit()
    customer_360.invalidate_customer_360(tenant_id, customer_id)
    return {"id": str(risk.id), "effective_level": risk.effective_level}


@app.get("/api/crm/customers/{customer_id}/risk", dependencies=[Depends(internal_service_auth)])
def risk_history(customer_id: UUID, tenant_id: UUID, session: Session = Depends(db)):
    tenant_item(session, Customer, customer_id, tenant_id, "customer")
    return [{"id": str(item.id), "level": item.level, "source": item.source, "reason": item.reason, "effective_level": item.effective_level, "override_level": item.override_level, "created_at": item.created_at} for item in risk_service.risk_history(session, tenant_id, customer_id)]


# ---------------------------------------------------------------------------
# External references, duplicates, audit
# ---------------------------------------------------------------------------

@app.post("/api/crm/customers/{customer_id}/external-references", dependencies=[Depends(internal_service_auth)])
def upsert_external_reference(customer_id: UUID, tenant_id: UUID, payload: ExternalReferenceIn, session: Session = Depends(db)):
    tenant_item(session, Customer, customer_id, tenant_id, "customer")
    reference = ExternalReference(tenant_id=tenant_id, customer_id=customer_id, **payload.model_dump())
    session.add(reference)
    session.commit()
    return {"id": str(reference.id)}


@app.get("/api/crm/duplicates", dependencies=[Depends(internal_service_auth)])
def duplicate_search(tenant_id: UUID, phone: str | None = None, email: str | None = None, caf_number: str | None = None, gstin: str | None = None, session: Session = Depends(db)):
    return duplicate_service.find_duplicate_customers(session, tenant_id, phone, email, caf_number, gstin)


@app.get("/api/crm/customers/{customer_id}/audit", dependencies=[Depends(internal_service_auth)])
def customer_audit(customer_id: UUID, tenant_id: UUID, limit: int = 100, offset: int = 0, session: Session = Depends(db)):
    tenant_item(session, Customer, customer_id, tenant_id, "customer")
    return [{"id": str(item.id), "action": item.action, "actor": item.actor, "safe_before": item.safe_before, "safe_after": item.safe_after, "reason": item.reason, "correlation_id": item.correlation_id, "created_at": item.created_at} for item in session.scalars(select(AuditLog).where(AuditLog.tenant_id == tenant_id, AuditLog.aggregate_id == str(customer_id)).order_by(AuditLog.created_at.desc()).offset(max(offset, 0)).limit(bounded(limit)))]


@app.get("/api/crm/audit", dependencies=[Depends(internal_service_auth)])
def audit_log(tenant_id: UUID, action: str | None = None, limit: int = 100, offset: int = 0, session: Session = Depends(db)):
    statement = select(AuditLog).where(AuditLog.tenant_id == tenant_id)
    if action:
        statement = statement.where(AuditLog.action == action)
    return [{"id": str(item.id), "action": item.action, "actor": item.actor, "aggregate_type": item.aggregate_type, "aggregate_id": item.aggregate_id, "reason": item.reason, "correlation_id": item.correlation_id, "created_at": item.created_at} for item in session.scalars(statement.order_by(AuditLog.created_at.desc()).offset(max(offset, 0)).limit(bounded(limit)))]


# ---------------------------------------------------------------------------
# Milestone 0 backward-compatible endpoints (minimal mapping preserved)
# ---------------------------------------------------------------------------

def _legacy_tenant(session: Session) -> Tenant:
    tenant = session.scalar(select(Tenant).where(Tenant.name == "legacy-default"))
    if tenant is None:
        tenant = Tenant(name="legacy-default")
        session.add(tenant)
        session.flush()
    return tenant


@app.post("/customers", status_code=201)
def create_customer_legacy(payload: CustomerCreate, session: Session = Depends(db)):
    tenant = _legacy_tenant(session)
    try:
        customer = customer_service.create_customer(session, tenant.id, {**payload.model_dump(), "full_name": payload.full_name or payload.legal_name}, "legacy")
    except Exception as error:
        raise _raise(error) from error
    session.commit()
    return {"id": str(customer.id), "customer_code": customer.customer_code, "full_name": customer.full_name, "phone": customer.phone, "email": customer.email, "status": customer.status}


@app.get("/customers")
def list_customers_legacy(limit: int = 100, offset: int = 0, session: Session = Depends(db)):
    tenant = _legacy_tenant(session)
    return [{"id": str(item.id), "customer_code": item.customer_code, "full_name": item.full_name, "phone": item.phone, "email": item.email, "status": item.status} for item in session.scalars(select(Customer).where(Customer.tenant_id == tenant.id).order_by(Customer.created_at.desc()).offset(max(offset, 0)).limit(bounded(limit)))]


@app.get("/customers/by-code/{customer_code}")
def get_customer_by_code_legacy(customer_code: str, session: Session = Depends(db)):
    tenant = _legacy_tenant(session)
    customer = session.scalar(select(Customer).where(Customer.tenant_id == tenant.id, Customer.customer_code == customer_code))
    if customer is None:
        raise HTTPException(404, "customer not found")
    return {"id": str(customer.id), "customer_code": customer.customer_code, "full_name": customer.full_name, "phone": customer.phone, "email": customer.email, "status": customer.status}


@app.get("/customers/{customer_id}")
def get_customer_legacy(customer_id: UUID, session: Session = Depends(db)):
    tenant = _legacy_tenant(session)
    customer = session.scalar(select(Customer).where(Customer.tenant_id == tenant.id, Customer.id == customer_id))
    if customer is None:
        raise HTTPException(404, "customer not found")
    return {"id": str(customer.id), "customer_code": customer.customer_code, "full_name": customer.full_name, "phone": customer.phone, "email": customer.email, "status": customer.status}


@app.post("/customers/{customer_id}/lifecycle-events")
def transition_customer_legacy(customer_id: UUID, payload: LifecycleTransitionIn, session: Session = Depends(db)):
    tenant = _legacy_tenant(session)
    customer = session.scalar(select(Customer).where(Customer.tenant_id == tenant.id, Customer.id == customer_id))
    if customer is None:
        raise HTTPException(404, "customer not found")
    try:
        customer = lifecycle_service.transition_customer(session, tenant.id, customer_id, payload.to_state, payload.trigger, "legacy", payload.reason, payload.related_external_type, payload.related_external_id)
    except Exception as error:
        raise _raise(error) from error
    session.commit()
    return {"id": str(customer.id), "status": customer.status, "lifecycle_state": customer.lifecycle_state}


@app.get("/customers/{customer_id}/kyc-documents")
def list_kyc_documents_legacy(customer_id: UUID, session: Session = Depends(db)):
    tenant = _legacy_tenant(session)
    customer = session.scalar(select(Customer).where(Customer.tenant_id == tenant.id, Customer.id == customer_id))
    if customer is None:
        raise HTTPException(404, "customer not found")
    return [{"id": str(item.id), "document_type": item.document_type, "masked_identifier": item.masked_identifier, "verification_state": item.verification_state} for item in session.scalars(select(KycDocument).where(KycDocument.tenant_id == tenant.id, KycDocument.customer_id == customer_id))]


@app.get("/leads")
def list_leads_legacy(limit: int = 100, offset: int = 0, session: Session = Depends(db)):
    tenant = _legacy_tenant(session)
    return [safe_lead(item) for item in session.scalars(select(Lead).where(Lead.tenant_id == tenant.id).order_by(Lead.created_at.desc()).offset(max(offset, 0)).limit(bounded(limit)))]


@app.post("/leads")
def create_lead_legacy(payload: LeadCreate, session: Session = Depends(db)):
    tenant = _legacy_tenant(session)
    try:
        lead = lead_service.capture_lead(session, tenant.id, payload.model_dump(), "legacy")
    except Exception as error:
        raise _raise(error) from error
    session.commit()
    return {"id": str(lead.id), "lead_number": lead.lead_number, "status": lead.stage}


@app.post("/franchises")
def create_franchise_legacy(payload: FranchiseIn, session: Session = Depends(db)):
    tenant = _legacy_tenant(session)
    item = Franchise(tenant_id=tenant.id, **payload.model_dump())
    session.add(item)
    session.commit()
    return {"id": str(item.id), "franchise_code": item.franchise_code, "status": item.status}


@app.post("/branches")
def create_branch_legacy(payload: BranchIn, session: Session = Depends(db)):
    tenant = _legacy_tenant(session)
    item = Branch(tenant_id=tenant.id, franchise_id=payload.franchise_id, branch_code=payload.branch_code, name=payload.name)
    session.add(item)
    session.commit()
    return {"id": str(item.id), "branch_code": item.branch_code}


# ===========================================================================
# Partners & ecosystem (Batch 6: 823, 825, 826, 400)
# ===========================================================================

@app.post("/api/crm/partners", status_code=201, dependencies=[Depends(internal_service_auth)])
def create_partner(payload: dict, tenant_id: UUID, request: Request, session: Session = Depends(db)):
    p = PartnerService.create(session, tenant_id, payload, actor_of(request))
    return {"id": str(p.id), "code": p.code, "name": p.name, "status": p.status}


@app.post("/api/crm/partners/{partner_id}/performance", dependencies=[Depends(internal_service_auth)])
def record_partner_performance(partner_id: UUID, payload: dict, tenant_id: UUID,
                               request: Request, session: Session = Depends(db)):
    row = PartnerService.record_performance(session, tenant_id, partner_id,
                                            payload.get("period", "MONTH"), payload.get("kpi", {}),
                                            actor_of(request))
    return {"id": str(row.id), "period": row.period, "kpi": row.kpi}


@app.post("/api/crm/partners/{partner_id}/sla/evaluate", dependencies=[Depends(internal_service_auth)])
def evaluate_partner_sla(partner_id: UUID, tenant_id: UUID, request: Request,
                         session: Session = Depends(db)):
    try:
        p = PartnerService.evaluate_sla(session, tenant_id, partner_id, actor_of(request))
    except KeyError:
        raise HTTPException(404, "partner not found")
    return {"id": str(p.id), "sla_pct": p.sla_pct, "performance_score": p.performance_score,
            "breaches": p.breaches}


@app.post("/api/crm/partners/{partner_id}/hierarchy", dependencies=[Depends(internal_service_auth)])
def add_partner_hierarchy(partner_id: UUID, payload: dict, tenant_id: UUID,
                          session: Session = Depends(db)):
    parent_id = UUID(str(payload["parent_id"])) if payload.get("parent_id") else None
    try:
        node = PartnerService.add_hierarchy(session, tenant_id, partner_id, parent_id)
    except KeyError as e:
        raise HTTPException(404, str(e))
    return {"partner_id": str(node.partner_id), "level": node.level,
            "parent_id": str(node.parent_id) if node.parent_id else None}


@app.get("/api/crm/partners/hierarchy/tree", dependencies=[Depends(internal_service_auth)])
def partner_hierarchy_tree(tenant_id: UUID, session: Session = Depends(db)):
    return PartnerService.tree(session, tenant_id)


@app.post("/api/crm/federations", status_code=201, dependencies=[Depends(internal_service_auth)])
def create_federation(payload: dict, tenant_id: UUID, request: Request,
                      session: Session = Depends(db)):
    link = FederationService.create_link(session, tenant_id, payload, actor_of(request))
    return {"id": str(link.id), "operator_name": link.operator_name, "direction": link.direction,
            "status": link.status}


@app.get("/api/crm/federations", dependencies=[Depends(internal_service_auth)])
def list_federations(tenant_id: UUID, session: Session = Depends(db)):
    rows = session.scalars(select(FederationLink).where(
        FederationLink.tenant_id == tenant_id)).all()
    return [{"id": str(l.id), "operator_name": l.operator_name, "direction": l.direction,
             "protocol": l.protocol, "status": l.status} for l in rows]


# ===========================================================================
# SLA timers, escalations, suggestions, regulatory (Batch 6: 310, 392, 391, 1191)
# ===========================================================================

@app.post("/api/crm/tickets/sla/start", status_code=201, dependencies=[Depends(internal_service_auth)])
def start_ticket_sla(payload: dict, tenant_id: UUID, request: Request,
                     session: Session = Depends(db)):
    t = TicketSlaService.start_timer(session, tenant_id, payload.get("ticket_id"),
                                     int(payload.get("sla_minutes", 240)), actor_of(request))
    return {"id": str(t.id), "ticket_id": t.ticket_id, "deadline": t.deadline, "breached": t.breached}


@app.post("/api/crm/tickets/sla/evaluate", dependencies=[Depends(internal_service_auth)])
def evaluate_ticket_sla(tenant_id: UUID, session: Session = Depends(db)):
    return {"breached": TicketSlaService.evaluate(session, tenant_id)}


@app.post("/api/crm/tickets/sla/resolve", dependencies=[Depends(internal_service_auth)])
def resolve_ticket_sla(payload: dict, tenant_id: UUID, session: Session = Depends(db)):
    try:
        t = TicketSlaService.resolve(session, tenant_id, payload.get("ticket_id"))
    except KeyError:
        raise HTTPException(404, "sla timer not found")
    return {"ticket_id": t.ticket_id, "breached": t.breached, "resolved_at": t.resolved_at}


@app.post("/api/crm/tickets/escalations", status_code=201, dependencies=[Depends(internal_service_auth)])
def escalate_ticket(payload: dict, tenant_id: UUID, request: Request,
                    session: Session = Depends(db)):
    e = EscalationService.escalate(session, tenant_id, payload.get("ticket_id"),
                                   payload.get("level", "LEVEL_1"), payload.get("reason"),
                                   actor_of(request))
    return {"id": str(e.id), "ticket_id": e.ticket_id, "level": e.level, "status": e.status}


@app.post("/api/crm/tickets/escalations/{escalation_id}/resolve", dependencies=[Depends(internal_service_auth)])
def resolve_ticket_escalation(escalation_id: UUID, tenant_id: UUID,
                              session: Session = Depends(db)):
    try:
        e = EscalationService.resolve(session, tenant_id, escalation_id)
    except KeyError:
        raise HTTPException(404, "escalation not found")
    return {"id": str(e.id), "status": e.status, "resolved_at": e.resolved_at}


@app.post("/api/crm/tickets/suggestions", status_code=201, dependencies=[Depends(internal_service_auth)])
def suggest_resolution(payload: dict, tenant_id: UUID, request: Request,
                       session: Session = Depends(db)):
    s = SuggestionService.suggest(session, tenant_id, payload.get("ticket_id"),
                                  payload.get("issue", ""), actor_of(request))
    return {"id": str(s.id), "ticket_id": s.ticket_id, "suggestion": s.suggestion,
            "source": s.source, "confidence": s.confidence}


@app.get("/api/crm/tickets/suggestions", dependencies=[Depends(internal_service_auth)])
def list_suggestions(tenant_id: UUID, ticket_id: str | None = None,
                     session: Session = Depends(db)):
    stmt = select(TicketSuggestion).where(TicketSuggestion.tenant_id == tenant_id)
    if ticket_id:
        stmt = stmt.where(TicketSuggestion.ticket_id == ticket_id)
    rows = session.scalars(stmt.order_by(TicketSuggestion.created_at.desc()).limit(100)).all()
    return [{"id": str(s.id), "ticket_id": s.ticket_id, "suggestion": s.suggestion,
             "source": s.source, "confidence": s.confidence} for s in rows]


@app.post("/api/crm/regulatory/track", status_code=201, dependencies=[Depends(internal_service_auth)])
def track_regulatory(payload: dict, tenant_id: UUID, request: Request,
                     session: Session = Depends(db)):
    r = RegulatoryService.track(session, tenant_id, payload.get("reseller_id"),
                                payload.get("report_type"), actor_of(request))
    return {"id": str(r.id), "reseller_id": r.reseller_id, "report_type": r.report_type,
            "status": r.status}


@app.post("/api/crm/regulatory/submit", dependencies=[Depends(internal_service_auth)])
def submit_regulatory(payload: dict, tenant_id: UUID, session: Session = Depends(db)):
    try:
        r = RegulatoryService.submit(session, tenant_id, payload.get("reseller_id"),
                                     payload.get("report_type"))
    except KeyError:
        raise HTTPException(404, "regulatory record not found")
    return {"id": str(r.id), "status": r.status, "submitted_at": r.submitted_at}


# ---------------------------------------------------------------------------
# KB feedback loop, experience recovery, behavioral loyalty (Batch 8)
# ---------------------------------------------------------------------------

@app.post("/api/crm/kb/feedback", status_code=201, dependencies=[Depends(internal_service_auth)])
def capture_kb_feedback(payload: dict, tenant_id: UUID, request: Request,
                        session: Session = Depends(db)):
    fb = KbService.capture(session, tenant_id, payload, actor_of(request))
    return {"id": str(fb.id), "article_id": fb.article_id, "rating": fb.rating,
            "helpful": fb.helpful, "applied": fb.applied}


@app.get("/api/crm/kb/feedback", dependencies=[Depends(internal_service_auth)])
def list_kb_feedback(tenant_id: UUID, session: Session = Depends(db)):
    rows = session.scalars(select(KbFeedback).where(KbFeedback.tenant_id == tenant_id)
                           .order_by(KbFeedback.created_at.desc()).limit(100)).all()
    return [{"id": str(f.id), "article_id": f.article_id, "rating": f.rating,
             "helpful": f.helpful, "applied": f.applied} for f in rows]


@app.post("/api/crm/kb/feedback/{feedback_id}/apply", dependencies=[Depends(internal_service_auth)])
def apply_kb_feedback(feedback_id: UUID, tenant_id: UUID, session: Session = Depends(db)):
    try:
        fb = KbService.apply(session, tenant_id, feedback_id)
    except KeyError:
        raise HTTPException(404, "feedback not found")
    return {"id": str(fb.id), "article_id": fb.article_id, "applied": fb.applied}


@app.post("/api/crm/recovery/trigger", status_code=201, dependencies=[Depends(internal_service_auth)])
def trigger_recovery(payload: dict, tenant_id: UUID, request: Request,
                     session: Session = Depends(db)):
    rec = RecoveryService.trigger(session, tenant_id, payload, actor_of(request))
    return {"id": str(rec.id), "customer_id": rec.customer_id, "metric": rec.metric,
            "recovery_action": rec.recovery_action, "status": rec.status}


@app.get("/api/crm/recovery", dependencies=[Depends(internal_service_auth)])
def list_recovery(tenant_id: UUID, session: Session = Depends(db)):
    rows = session.scalars(select(ExperienceRecovery).where(
        ExperienceRecovery.tenant_id == tenant_id)
        .order_by(ExperienceRecovery.created_at.desc()).limit(100)).all()
    return [{"id": str(r.id), "customer_id": r.customer_id, "metric": r.metric,
             "recovery_action": r.recovery_action, "status": r.status} for r in rows]


@app.post("/api/crm/loyalty/score", status_code=201, dependencies=[Depends(internal_service_auth)])
def calculate_loyalty(payload: dict, tenant_id: UUID, request: Request,
                      session: Session = Depends(db)):
    ls = LoyaltyService.score(session, tenant_id, payload, actor_of(request))
    return {"id": str(ls.id), "customer_id": ls.customer_id, "period": ls.period,
            "score": ls.score, "behavioral_factors": ls.behavioral_factors}


@app.get("/api/crm/loyalty", dependencies=[Depends(internal_service_auth)])
def list_loyalty(tenant_id: UUID, customer_id: str | None = None,
                 session: Session = Depends(db)):
    stmt = select(LoyaltyScore).where(LoyaltyScore.tenant_id == tenant_id)
    if customer_id:
        stmt = stmt.where(LoyaltyScore.customer_id == customer_id)
    rows = session.scalars(stmt.order_by(LoyaltyScore.created_at.desc()).limit(100)).all()
    return [{"id": str(l.id), "customer_id": l.customer_id, "period": l.period,
             "score": l.score} for l in rows]
