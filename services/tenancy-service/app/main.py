"""Tenancy Service API (Milestone 8).

Business-facing control plane for multi-tenant & franchise management. All
tenant-owned operations require a validated TenantContext; tenant_id query
params are reconciled against the authenticated principal and any conflict is
rejected."""
from contextlib import asynccontextmanager
import secrets as _secrets
from datetime import datetime
from os import getenv
import uuid
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import models  # noqa: F401
from .context import require_tenant
from .database import Base, SessionLocal, engine
from .domain.exceptions import TenancyError
from .models import AuditLog, Tenant
from .schemas import (
    AdjustmentIn,
    AggregateIn,
    AgreementIn,
    AgreementVersionIn,
    ApprovalDecisionIn,
    ApprovalIn,
    ClawbackIn,
    CommissionAgreementIn,
    CommissionPlanIn,
    CommissionRuleIn,
    ConfigIn,
    CredentialIn,
    CycleIn,
    DisputeIn,
    DisputeResolveIn,
    DomainIn,
    DomainVerifyIn,
    EarningIn,
    EntitlementIn,
    ExportIn,
    FeatureIn,
    GrantIn,
    ImpersonationIn,
    MembershipIn,
    OrgUnitCreate,
    OrgUnitReparent,
    OwnershipIn,
    PartnerCreate,
    PartnerLinkIn,
    PartnerMembershipIn,
    PartnerStatusIn,
    PayoutIn,
    QuotaIn,
    ReconcileIn,
    ReportIn,
    RoleAssignIn,
    RoleIn,
    RolePermissionsIn,
    SecretIn,
    ServiceAccountIn,
    ServiceScopeIn,
    SettlementIn,
    TenantCreate,
    TenantStatusIn,
    TerritoryIn,
    TransferApproveIn,
    TransferIn,
    WalletIn,
)
from .security import internal_service_auth, management_auth
from .services import (
    access_service,
    audit_service,
    catalog_service,
    commission_service,
    organization_service,
    report_service,
    settlement_service,
    tenant_service,
    wallet_service,
)
from .services.governance_service import (CampaignService, CoreAiService, GovernanceService,
                                          LabService, NotificationService)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        catalog_service.ensure_defaults(session)
        session.commit()
    finally:
        session.close()
    yield


app = FastAPI(title="Tenancy Service", version="8.0.0", lifespan=lifespan)


@app.exception_handler(TenancyError)
async def _tenancy_error_handler(_request: Request, exc: TenancyError):
    return JSONResponse(status_code=exc.status_code,
                        content={"code": exc.code, "detail": exc.message})


def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _raise(error: Exception) -> None:
    if isinstance(error, TenancyError):
        raise HTTPException(error.status_code, {"code": error.code, "detail": error.message}) from error
    raise HTTPException(422, str(error)) from error


def _actor(request: Request) -> str:
    principal = getattr(request.state, "tenancy_principal", None)
    if principal:
        return principal["subject"]
    return "system"


def _tid(tenant_id: UUID | None) -> UUID:
    from .security import current_tenant

    ctx = current_tenant.get()
    if tenant_id is not None:
        if ctx is not None and ctx.tenant_id is not None and \
                not _secrets.compare_digest(str(tenant_id), str(ctx.tenant_id)):
            raise HTTPException(403, "tenant access denied")
        return tenant_id
    if ctx is not None and ctx.tenant_id is not None:
        return ctx.tenant_id
    raise HTTPException(422, "tenant_id is required")


def _run(session: Session, fn, request: Request):
    try:
        result = fn()
        session.commit()
        return result
    except TenancyError as error:
        session.rollback()
        _raise(error)


def _tenant_or_404(session: Session, tenant_id) -> Tenant:
    tenant = session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(404, "tenant not found")
    return tenant


# ===========================================================================
# Health / status
# ===========================================================================
@app.get("/health")
def health():
    return {"status": "ok", "service": getenv("SERVICE_NAME", "tenancy-service")}


@app.get("/status")
def service_status():
    return {"service": "tenancy", "phase": "milestone-8", "version": "8.0.0"}


# ===========================================================================
# Tenants
# ===========================================================================
@app.post("/api/tenancy/tenants", status_code=status.HTTP_201_CREATED, dependencies=[Depends(management_auth)])
def create_tenant(payload: TenantCreate, request: Request = None, session: Session = Depends(db)):
    def fn():
        tenant = tenant_service.create_tenant(
            session, name=payload.name, code=payload.code, currency=payload.currency,
            country=payload.country, legal_name=payload.legal_name,
            isolation_mode=payload.isolation_mode, requested_by=_actor(request))
        return {"id": str(tenant.id), "code": tenant.code, "status": tenant.status}
    return _run(session, fn, request)


@app.post("/api/tenancy/tenants/{tenant_id}/validate", dependencies=[Depends(management_auth)])
def validate_tenant(tenant_id: UUID, request: Request = None, session: Session = Depends(db)):
    def fn():
        tenant = tenant_service.validate_tenant(session, tenant_id, actor=_actor(request))
        return {"id": str(tenant.id), "status": tenant.status}
    return _run(session, fn, request)


@app.post("/api/tenancy/tenants/{tenant_id}/provision", dependencies=[Depends(management_auth)])
def provision_tenant(tenant_id: UUID, request: Request = None, session: Session = Depends(db)):
    def fn():
        tenant = tenant_service.provision_tenant(session, tenant_id, actor=_actor(request))
        return {"id": str(tenant.id), "status": tenant.status, "provision_state": tenant.provision_state}
    return _run(session, fn, request)


@app.get("/api/tenancy/tenants/{tenant_id}/provision", dependencies=[Depends(management_auth)])
def provision_progress(tenant_id: UUID, session: Session = Depends(db)):
    tenant = _tenant_or_404(session, tenant_id)
    return {"id": str(tenant.id), "status": tenant.status, "provision_state": tenant.provision_state}


@app.get("/api/tenancy/tenants", dependencies=[Depends(management_auth)])
def list_tenants(request: Request = None, session: Session = Depends(db)):
    ctx = require_tenant()
    stmt = select(Tenant).order_by(Tenant.created_at.desc())
    if ctx.tenant_id is not None:
        stmt = stmt.where(Tenant.id == ctx.tenant_id)
    rows = list(session.scalars(stmt.limit(200)))
    return [{"id": str(t.id), "code": t.code, "name": t.name, "status": t.status,
             "isolation_mode": t.isolation_mode} for t in rows]


@app.get("/api/tenancy/tenants/{tenant_id}", dependencies=[Depends(management_auth)])
def tenant_detail(tenant_id: UUID, session: Session = Depends(db)):
    tenant = tenant_service.get_tenant_or_404(session, _tid(tenant_id))
    return {"id": str(tenant.id), "code": tenant.code, "name": tenant.name, "legal_name": tenant.legal_name,
            "currency": tenant.currency, "country": tenant.country, "status": tenant.status,
            "isolation_mode": tenant.isolation_mode, "provision_state": tenant.provision_state,
            "activated_at": tenant.activated_at.isoformat() if tenant.activated_at else None}


@app.post("/api/tenancy/tenants/{tenant_id}/activate", dependencies=[Depends(management_auth)])
def activate_tenant(tenant_id: UUID, request: Request = None, session: Session = Depends(db)):
    def fn():
        tenant_service.provision_tenant(session, tenant_id)
        tenant = tenant_service.get_tenant_or_404(session, tenant_id)
        return {"id": str(tenant.id), "status": tenant.status}
    return _run(session, fn, request)


@app.post("/api/tenancy/tenants/{tenant_id}/suspend", dependencies=[Depends(management_auth)])
def suspend_tenant(tenant_id: UUID, payload: TenantStatusIn, request: Request = None,
                   session: Session = Depends(db)):
    def fn():
        tenant = tenant_service.suspend_tenant(session, tenant_id, reason=payload.reason,
                                               scope=payload.scope, actor=_actor(request))
        return {"id": str(tenant.id), "status": tenant.status}
    return _run(session, fn, request)


@app.post("/api/tenancy/tenants/{tenant_id}/resume", dependencies=[Depends(management_auth)])
def resume_tenant(tenant_id: UUID, request: Request = None, session: Session = Depends(db)):
    def fn():
        tenant = tenant_service.resume_tenant(session, tenant_id, actor=_actor(request))
        return {"id": str(tenant.id), "status": tenant.status}
    return _run(session, fn, request)


@app.post("/api/tenancy/tenants/{tenant_id}/restrict", dependencies=[Depends(management_auth)])
def restrict_tenant(tenant_id: UUID, request: Request = None, session: Session = Depends(db)):
    def fn():
        tenant = tenant_service.restrict_tenant(session, tenant_id, actor=_actor(request))
        return {"id": str(tenant.id), "status": tenant.status}
    return _run(session, fn, request)


@app.post("/api/tenancy/tenants/{tenant_id}/offboard", dependencies=[Depends(management_auth)])
def offboard_tenant(tenant_id: UUID, payload: TenantStatusIn, request: Request = None,
                    session: Session = Depends(db)):
    def fn():
        tenant = tenant_service.start_offboarding(session, tenant_id, reason=payload.reason,
                                                  actor=_actor(request))
        return {"id": str(tenant.id), "status": tenant.status}
    return _run(session, fn, request)


@app.post("/api/tenancy/tenants/{tenant_id}/archive", dependencies=[Depends(management_auth)])
def archive_tenant(tenant_id: UUID, request: Request = None, session: Session = Depends(db)):
    def fn():
        tenant = tenant_service.archive_tenant(session, tenant_id, actor=_actor(request))
        return {"id": str(tenant.id), "status": tenant.status}
    return _run(session, fn, request)


@app.get("/api/tenancy/tenants/{tenant_id}/health", dependencies=[Depends(management_auth)])
def tenant_health(tenant_id: UUID, session: Session = Depends(db)):
    return tenant_service.tenant_health(session, _tid(tenant_id))


# ===========================================================================
# Tenant configuration, domains, features, entitlements, quotas, secrets
# ===========================================================================
@app.get("/api/tenancy/tenants/{tenant_id}/config", dependencies=[Depends(management_auth)])
def get_config(tenant_id: UUID, category: str = Query(default="all"), session: Session = Depends(db)):
    tid = _tid(tenant_id)
    if category == "all":
        return {"tenant_id": str(tid),
                "config": {c: tenant_service.get_config(session, tid, c) for c in
                           ("legal", "locale", "tax", "portal", "notifications", "security")}}
    return {"tenant_id": str(tid), "category": category,
            "config": tenant_service.get_config(session, tid, category)}


@app.put("/api/tenancy/tenants/{tenant_id}/config", dependencies=[Depends(management_auth)])
def set_config(tenant_id: UUID, payload: ConfigIn, request: Request = None, session: Session = Depends(db)):
    def fn():
        row = tenant_service.set_config(session, _tid(tenant_id), payload.category, payload.config,
                                        actor=_actor(request))
        return {"id": str(row.id), "category": row.category, "version": row.version}
    return _run(session, fn, request)


@app.post("/api/tenancy/tenants/{tenant_id}/domains", status_code=status.HTTP_201_CREATED,
          dependencies=[Depends(management_auth)])
def add_domain(tenant_id: UUID, payload: DomainIn, request: Request = None, session: Session = Depends(db)):
    def fn():
        row = tenant_service.add_domain(session, _tid(tenant_id), payload.domain,
                                        is_primary=payload.is_primary, actor=_actor(request))
        return {"id": str(row.id), "domain": row.domain, "verification_token": row.verification_token,
                "status": row.status}
    return _run(session, fn, request)


@app.post("/api/tenancy/tenants/{tenant_id}/domains/{domain_id}/verify", dependencies=[Depends(management_auth)])
def verify_domain(tenant_id: UUID, domain_id: UUID, payload: DomainVerifyIn,
                  session: Session = Depends(db)):
    def fn():
        row = tenant_service.verify_domain(session, _tid(tenant_id), domain_id, token=payload.token)
        return {"id": str(row.id), "domain": row.domain, "status": row.status}
    return _run(session, fn, Request)


@app.post("/api/tenancy/tenants/{tenant_id}/features", dependencies=[Depends(management_auth)])
def set_feature(tenant_id: UUID, payload: FeatureIn, request: Request = None, session: Session = Depends(db)):
    def fn():
        tenant_service.set_feature(session, _tid(tenant_id), payload.code, payload.enabled,
                                   actor=_actor(request))
        return {"code": payload.code, "enabled": payload.enabled}
    return _run(session, fn, request)


@app.post("/api/tenancy/tenants/{tenant_id}/entitlements", dependencies=[Depends(management_auth)])
def grant_entitlement(tenant_id: UUID, payload: EntitlementIn, request: Request = None,
                      session: Session = Depends(db)):
    def fn():
        tenant_service.grant_entitlement(session, _tid(tenant_id), payload.code, quantity=payload.quantity,
                                         granted_by=_actor(request))
        return {"code": payload.code, "granted": True}
    return _run(session, fn, request)


@app.post("/api/tenancy/tenants/{tenant_id}/quotas", dependencies=[Depends(management_auth)])
def set_quota(tenant_id: UUID, payload: QuotaIn, request: Request = None, session: Session = Depends(db)):
    def fn():
        tenant_service.set_quota(session, _tid(tenant_id), payload.kind, payload.limit, actor=_actor(request))
        return {"kind": payload.kind, "limit": payload.limit}
    return _run(session, fn, request)


@app.post("/api/tenancy/tenants/{tenant_id}/secrets", dependencies=[Depends(management_auth)])
def set_secret(tenant_id: UUID, payload: SecretIn, request: Request = None, session: Session = Depends(db)):
    from ..domain.secrets import encrypt_secret
    from ..models import TenantSecret

    def fn():
        tid = _tid(tenant_id)
        row = session.scalars(select(TenantSecret).where(
            TenantSecret.tenant_id == tid, TenantSecret.name == payload.name)).first()
        if row is None:
            row = TenantSecret(tenant_id=tid, name=payload.name, secret_ref=encrypt_secret(payload.value),
                               category=payload.category)
            session.add(row)
        else:
            row.secret_ref = encrypt_secret(payload.value)
        session.flush()
        return {"name": payload.name, "stored": True}
    return _run(session, fn, request)


# ===========================================================================
# Organization units
# ===========================================================================
@app.post("/api/tenancy/tenants/{tenant_id}/org-units", status_code=status.HTTP_201_CREATED,
          dependencies=[Depends(management_auth)])
def create_org_unit(tenant_id: UUID, payload: OrgUnitCreate, request: Request = None,
                    session: Session = Depends(db)):
    def fn():
        unit = organization_service.create_org_unit(
            session, _tid(tenant_id), unit_type=payload.unit_type, code=payload.code, name=payload.name,
            parent_id=UUID(payload.parent_id) if payload.parent_id else None, actor=_actor(request))
        return {"id": str(unit.id), "code": unit.code, "path": unit.path}
    return _run(session, fn, request)


@app.post("/api/tenancy/tenants/{tenant_id}/org-units/{org_unit_id}/reparent", dependencies=[Depends(management_auth)])
def reparent_org_unit(tenant_id: UUID, org_unit_id: UUID, payload: OrgUnitReparent,
                      request: Request = None, session: Session = Depends(db)):
    def fn():
        unit = organization_service.reparent_org_unit(
            session, _tid(tenant_id), org_unit_id,
            new_parent_id=UUID(payload.new_parent_id) if payload.new_parent_id else None, actor=_actor(request))
        return {"id": str(unit.id), "path": unit.path}
    return _run(session, fn, request)


@app.get("/api/tenancy/tenants/{tenant_id}/org-units", dependencies=[Depends(management_auth)])
def list_org_units(tenant_id: UUID, parent_id: UUID | None = Query(default=None), session: Session = Depends(db)):
    rows = organization_service.list_org_units(session, _tid(tenant_id), parent_id=parent_id)
    return [{"id": str(u.id), "code": u.code, "name": u.name, "unit_type": u.unit_type,
             "path": u.path} for u in rows]


# ===========================================================================
# Partners
# ===========================================================================
@app.post("/api/tenancy/tenants/{tenant_id}/partners", status_code=status.HTTP_201_CREATED,
          dependencies=[Depends(management_auth)])
def create_partner(tenant_id: UUID, payload: PartnerCreate, request: Request = None,
                   session: Session = Depends(db)):
    def fn():
        partner = organization_service.create_partner(
            session, _tid(tenant_id), partner_type=payload.partner_type, code=payload.code,
            name=payload.name, org_unit_id=UUID(payload.org_unit_id) if payload.org_unit_id else None,
            contact_person=payload.contact_person, email=payload.email, phone=payload.phone,
            currency=payload.currency, actor=_actor(request))
        return {"id": str(partner.id), "code": partner.code, "status": partner.status}
    return _run(session, fn, request)


@app.post("/api/tenancy/tenants/{tenant_id}/partners/{partner_id}/status", dependencies=[Depends(management_auth)])
def change_partner_status(tenant_id: UUID, partner_id: UUID, payload: PartnerStatusIn,
                          request: Request = None, session: Session = Depends(db)):
    def fn():
        partner = organization_service.change_partner_status(
            session, _tid(tenant_id), partner_id, to_status=payload.to_status, reason=payload.reason,
            actor=_actor(request))
        return {"id": str(partner.id), "status": partner.status}
    return _run(session, fn, request)


@app.post("/api/tenancy/tenants/{tenant_id}/partners/{partner_id}/link", dependencies=[Depends(management_auth)])
def link_partner(tenant_id: UUID, partner_id: UUID, payload: PartnerLinkIn,
                 request: Request = None, session: Session = Depends(db)):
    def fn():
        row = organization_service.link_partners(
            session, _tid(tenant_id), partner_id, UUID(payload.child_partner_id),
            relationship_type=payload.relationship_type, actor=_actor(request))
        return {"id": str(row.id), "relationship_type": row.relationship_type}
    return _run(session, fn, request)


@app.post("/api/tenancy/tenants/{tenant_id}/partners/{partner_id}/agreements",
          status_code=status.HTTP_201_CREATED, dependencies=[Depends(management_auth)])
def create_agreement(tenant_id: UUID, partner_id: UUID, payload: AgreementIn,
                     request: Request = None, session: Session = Depends(db)):
    def fn():
        row = organization_service.create_agreement(
            session, _tid(tenant_id), partner_id=partner_id, code=payload.code,
            customer_ownership_model=payload.customer_ownership_model, actor=_actor(request))
        return {"id": str(row.id), "code": row.code, "status": row.status}
    return _run(session, fn, request)


@app.post("/api/tenancy/tenants/{tenant_id}/agreements/{agreement_id}/versions", dependencies=[Depends(management_auth)])
def add_agreement_version(tenant_id: UUID, agreement_id: UUID, payload: AgreementVersionIn,
                          request: Request = None, session: Session = Depends(db)):
    def fn():
        row = organization_service.add_agreement_version(session, _tid(tenant_id), agreement_id,
                                                         terms=payload.terms, actor=_actor(request))
        return {"id": str(row.id), "version": row.version}
    return _run(session, fn, request)


@app.post("/api/tenancy/tenants/{tenant_id}/partners/{partner_id}/service-scopes", dependencies=[Depends(management_auth)])
def add_service_scope(tenant_id: UUID, partner_id: UUID, payload: ServiceScopeIn,
                      request: Request = None, session: Session = Depends(db)):
    def fn():
        row = organization_service.add_service_scope(session, _tid(tenant_id), partner_id,
                                                     service=payload.service, enabled=payload.enabled,
                                                     detail=payload.detail, actor=_actor(request))
        return {"id": str(row.id), "service": row.service, "enabled": row.enabled}
    return _run(session, fn, request)


@app.post("/api/tenancy/tenants/{tenant_id}/partners/{partner_id}/territories", dependencies=[Depends(management_auth)])
def add_territory(tenant_id: UUID, partner_id: UUID, payload: TerritoryIn,
                  request: Request = None, session: Session = Depends(db)):
    def fn():
        row = organization_service.add_territory(session, _tid(tenant_id), partner_id,
                                                 territory_key=payload.territory_key, region=payload.region,
                                                 is_primary=payload.is_primary, actor=_actor(request))
        return {"id": str(row.id), "territory_key": row.territory_key}
    return _run(session, fn, request)


@app.post("/api/tenancy/tenants/{tenant_id}/partners/{partner_id}/memberships", dependencies=[Depends(management_auth)])
def add_partner_membership(tenant_id: UUID, partner_id: UUID, payload: PartnerMembershipIn,
                           request: Request = None, session: Session = Depends(db)):
    def fn():
        row = organization_service.add_partner_membership(session, _tid(tenant_id), partner_id,
                                                          user_id=payload.user_id, role=payload.role,
                                                          granted_by=_actor(request))
        return {"id": str(row.id), "user_id": row.user_id, "role": row.role}
    return _run(session, fn, request)


@app.get("/api/tenancy/tenants/{tenant_id}/partners", dependencies=[Depends(management_auth)])
def list_partners(tenant_id: UUID, session: Session = Depends(db)):
    from ..models import Partner

    rows = list(session.scalars(select(Partner).where(Partner.tenant_id == _tid(tenant_id))
                                .order_by(Partner.created_at)))
    return [{"id": str(p.id), "code": p.code, "name": p.name, "partner_type": p.partner_type,
             "status": p.status} for p in rows]


# ===========================================================================
# Ownership / transfers / grants
# ===========================================================================
@app.post("/api/tenancy/tenants/{tenant_id}/ownership", dependencies=[Depends(management_auth)])
def set_ownership(tenant_id: UUID, payload: OwnershipIn, request: Request = None,
                  session: Session = Depends(db)):
    def fn():
        row = organization_service.set_ownership(
            session, _tid(tenant_id), customer_id=payload.customer_id,
            owning_org_unit_id=UUID(payload.owning_org_unit_id) if payload.owning_org_unit_id else None,
            acquisition_partner_id=UUID(payload.acquisition_partner_id) if payload.acquisition_partner_id else None,
            servicing_partner_id=UUID(payload.servicing_partner_id) if payload.servicing_partner_id else None,
            billing_owner_id=UUID(payload.billing_owner_id) if payload.billing_owner_id else None,
            support_owner_id=UUID(payload.support_owner_id) if payload.support_owner_id else None,
            network_owner_id=UUID(payload.network_owner_id) if payload.network_owner_id else None,
            collection_owner_id=UUID(payload.collection_owner_id) if payload.collection_owner_id else None,
            actor=_actor(request))
        return {"id": str(row.id), "customer_id": row.customer_id}
    return _run(session, fn, request)


@app.post("/api/tenancy/tenants/{tenant_id}/transfers", status_code=status.HTTP_201_CREATED,
          dependencies=[Depends(management_auth)])
def request_transfer(tenant_id: UUID, payload: TransferIn, request: Request = None,
                     session: Session = Depends(db)):
    def fn():
        row = organization_service.transfer_customer(
            session, _tid(tenant_id), customer_id=payload.customer_id,
            to_owner_id=UUID(payload.to_owner_id) if payload.to_owner_id else None,
            transfer_type=payload.transfer_type, reason=payload.reason, requested_by=_actor(request))
        return {"id": str(row.id), "customer_id": row.customer_id, "state": row.state}
    return _run(session, fn, request)


@app.post("/api/tenancy/tenants/{tenant_id}/transfers/{transfer_id}/approve", dependencies=[Depends(management_auth)])
def approve_transfer(tenant_id: UUID, transfer_id: UUID, payload: TransferApproveIn,
                     session: Session = Depends(db)):
    def fn():
        row = organization_service.approve_transfer(session, _tid(tenant_id), transfer_id,
                                                    approved_by=payload.approved_by)
        return {"id": str(row.id), "state": row.state}
    return _run(session, fn, Request)


@app.post("/api/tenancy/tenants/{tenant_id}/grants", status_code=status.HTTP_201_CREATED,
          dependencies=[Depends(management_auth)])
def create_grant(tenant_id: UUID, payload: GrantIn, request: Request = None, session: Session = Depends(db)):
    def fn():
        row = organization_service.create_grant(
            session, _tid(tenant_id), granting_org_unit_id=UUID(payload.granting_org_unit_id),
            receiving_org_unit_id=UUID(payload.receiving_org_unit_id), resource_type=payload.resource_type,
            resource_scope=payload.resource_scope, permission=payload.permission, purpose=payload.purpose,
            ends_at=datetime.fromisoformat(payload.ends_at) if payload.ends_at else None,
            approved_by=payload.approved_by, actor=_actor(request))
        return {"id": str(row.id)}
    return _run(session, fn, request)


# ===========================================================================
# Access control
# ===========================================================================
@app.post("/api/tenancy/tenants/{tenant_id}/memberships", status_code=status.HTTP_201_CREATED,
          dependencies=[Depends(management_auth)])
def create_membership(tenant_id: UUID, payload: MembershipIn, request: Request = None,
                      session: Session = Depends(db)):
    def fn():
        row = access_service.create_membership(session, _tid(tenant_id), user_id=payload.user_id,
                                               granted_by=_actor(request))
        return {"id": str(row.id), "user_id": row.user_id, "status": row.status}
    return _run(session, fn, request)


@app.post("/api/tenancy/tenants/{tenant_id}/roles", status_code=status.HTTP_201_CREATED,
          dependencies=[Depends(management_auth)])
def create_role(tenant_id: UUID, payload: RoleIn, request: Request = None, session: Session = Depends(db)):
    def fn():
        row = access_service.create_role(session, _tid(tenant_id), code=payload.code, name=payload.name,
                                         actor=_actor(request))
        return {"id": str(row.id), "code": row.code}
    return _run(session, fn, request)


@app.put("/api/tenancy/tenants/{tenant_id}/roles/{role_id}/permissions", dependencies=[Depends(management_auth)])
def set_role_permissions(tenant_id: UUID, role_id: UUID, payload: RolePermissionsIn,
                         request: Request = None, session: Session = Depends(db)):
    def fn():
        role = access_service.set_role_permissions(session, _tid(tenant_id), role_id,
                                                   permission_codes=payload.permission_codes,
                                                   actor=_actor(request))
        return {"id": str(role.id), "permissions": payload.permission_codes}
    return _run(session, fn, request)


@app.post("/api/tenancy/tenants/{tenant_id}/role-assignments", dependencies=[Depends(management_auth)])
def assign_role(tenant_id: UUID, payload: RoleAssignIn, request: Request = None, session: Session = Depends(db)):
    def fn():
        row = access_service.assign_role(
            session, _tid(tenant_id), membership_id=UUID(payload.membership_id),
            role_id=UUID(payload.role_id), org_unit_id=UUID(payload.org_unit_id) if payload.org_unit_id else None,
            scope_kind=payload.scope_kind, assigned_by=payload.assigned_by or _actor(request))
        return {"id": str(row.id), "scope_kind": row.scope_kind}
    return _run(session, fn, request)


@app.get("/api/tenancy/permissions", dependencies=[Depends(management_auth)])
def permission_catalog(session: Session = Depends(db)):
    from ..models import Permission

    rows = list(session.scalars(select(Permission).order_by(Permission.code)))
    return {"permissions": [p.code for p in rows]}


@app.post("/api/tenancy/tenants/{tenant_id}/approvals", status_code=status.HTTP_201_CREATED,
          dependencies=[Depends(management_auth)])
def request_approval(tenant_id: UUID, payload: ApprovalIn, request: Request = None,
                     session: Session = Depends(db)):
    def fn():
        row = access_service.request_approval(
            session, _tid(tenant_id), operation=payload.operation, requested_by=_actor(request),
            reason=payload.reason, detail=payload.detail, resource_type=payload.resource_type,
            resource_id=payload.resource_id)
        return {"id": str(row.id), "operation": row.operation, "state": row.state}
    return _run(session, fn, request)


@app.post("/api/tenancy/tenants/{tenant_id}/approvals/{approval_id}/decide", dependencies=[Depends(management_auth)])
def decide_approval(tenant_id: UUID, approval_id: UUID, payload: ApprovalDecisionIn,
                    request: Request = None, session: Session = Depends(db)):
    def fn():
        row = access_service.decide_approval(session, _tid(tenant_id), approval_id, decision=payload.decision,
                                             decided_by=payload.decided_by, reason=payload.reason)
        return {"id": str(row.id), "state": row.state}
    return _run(session, fn, request)


@app.post("/api/tenancy/tenants/{tenant_id}/service-accounts", status_code=status.HTTP_201_CREATED,
          dependencies=[Depends(management_auth)])
def create_service_account(tenant_id: UUID, payload: ServiceAccountIn, request: Request = None,
                           session: Session = Depends(db)):
    def fn():
        row = access_service.create_service_account(
            session, _tid(tenant_id), service=payload.service, name=payload.name,
            permission_codes=payload.permission_codes, ip_restrictions=payload.ip_restrictions,
            created_by=_actor(request))
        return {"id": str(row.id), "service": row.service}
    return _run(session, fn, request)


@app.post("/api/tenancy/tenants/{tenant_id}/api-credentials", status_code=status.HTTP_201_CREATED,
          dependencies=[Depends(management_auth)])
def issue_api_credential(tenant_id: UUID, payload: CredentialIn, request: Request = None,
                         session: Session = Depends(db)):
    def fn():
        from ..domain.secrets import decrypt_secret

        row = access_service.issue_api_credential(session, _tid(tenant_id),
                                                  service_account_id=UUID(payload.service_account_id),
                                                  name=payload.name, expires_in_days=payload.expires_in_days,
                                                  actor=_actor(request))
        return {"id": str(row.id), "secret": decrypt_secret(row.secret_ref), "expires_at":
                row.expires_at.isoformat() if row.expires_at else None}
    return _run(session, fn, request)


@app.post("/api/tenancy/tenants/{tenant_id}/impersonation", status_code=status.HTTP_201_CREATED,
          dependencies=[Depends(management_auth)])
def start_impersonation(tenant_id: UUID, payload: ImpersonationIn, request: Request = None,
                        session: Session = Depends(db)):
    def fn():
        row = access_service.start_impersonation(
            session, _tid(tenant_id), admin_user=_actor(request), target_user=payload.target_user,
            reason=payload.reason, ticket_ref=payload.ticket_ref, read_only=payload.read_only,
            ttl_minutes=payload.ttl_minutes)
        return {"id": str(row.id), "state": row.state, "read_only": row.read_only,
                "expires_at": row.expires_at.isoformat() if row.expires_at else None}
    return _run(session, fn, request)


# ===========================================================================
# Commissions
# ===========================================================================
@app.post("/api/tenancy/tenants/{tenant_id}/commission-plans", status_code=status.HTTP_201_CREATED,
          dependencies=[Depends(management_auth)])
def create_commission_plan(tenant_id: UUID, payload: CommissionPlanIn, request: Request = None,
                           session: Session = Depends(db)):
    def fn():
        row = commission_service.create_plan(session, _tid(tenant_id), code=payload.code, name=payload.name,
                                             actor=_actor(request))
        return {"id": str(row.id), "code": row.code}
    return _run(session, fn, request)


@app.post("/api/tenancy/tenants/{tenant_id}/commission-plans/{plan_id}/approve", dependencies=[Depends(management_auth)])
def approve_commission_plan(tenant_id: UUID, plan_id: UUID, request: Request = None,
                            session: Session = Depends(db)):
    def fn():
        row = commission_service.approve_plan(session, _tid(tenant_id), plan_id, approved_by=_actor(request))
        return {"id": str(row.id), "status": row.status}
    return _run(session, fn, request)


@app.post("/api/tenancy/tenants/{tenant_id}/commission-plans/{plan_id}/rules", status_code=status.HTTP_201_CREATED,
          dependencies=[Depends(management_auth)])
def add_commission_rule(tenant_id: UUID, plan_id: UUID, payload: CommissionRuleIn,
                        request: Request = None, session: Session = Depends(db)):
    def fn():
        row = commission_service.add_rule(
            session, _tid(tenant_id), plan_id, code=payload.code, name=payload.name, basis=payload.basis,
            calculation_type=payload.calculation_type, rate=payload.rate, fixed_amount=payload.fixed_amount,
            currency=payload.currency, tiers=payload.tiers, slabs=payload.slabs,
            exclusions=payload.exclusions, threshold=payload.threshold, multiplier=payload.multiplier,
            actor=_actor(request))
        return {"id": str(row.id), "code": row.code, "calculation_type": row.calculation_type}
    return _run(session, fn, request)


@app.post("/api/tenancy/tenants/{tenant_id}/commission-agreements", status_code=status.HTTP_201_CREATED,
          dependencies=[Depends(management_auth)])
def create_commission_agreement(tenant_id: UUID, payload: CommissionAgreementIn,
                                request: Request = None, session: Session = Depends(db)):
    def fn():
        row = commission_service.create_agreement(session, _tid(tenant_id), partner_id=UUID(payload.partner_id),
                                                  plan_id=UUID(payload.plan_id), actor=_actor(request))
        return {"id": str(row.id), "status": row.status}
    return _run(session, fn, request)


@app.post("/api/tenancy/tenants/{tenant_id}/commission-earnings", status_code=status.HTTP_201_CREATED,
          dependencies=[Depends(management_auth)])
def recognize_earning(tenant_id: UUID, payload: EarningIn, request: Request = None,
                      session: Session = Depends(db)):
    def fn():
        row = commission_service.recognize_earning(
            session, _tid(tenant_id), partner_id=UUID(payload.partner_id),
            source_event_id=payload.source_event_id, source_event_type=payload.source_event_type,
            basis=payload.basis, basis_amount=payload.basis_amount, customer_id=payload.customer_id,
            service_id=payload.service_id, invoice_ref=payload.invoice_ref, payment_ref=payload.payment_ref,
            currency=payload.currency, actor=_actor(request))
        return {"id": str(row.id), "amount": row.amount, "status": row.status, "formula": row.rate_formula}
    return _run(session, fn, request)


@app.post("/api/tenancy/tenants/{tenant_id}/commission-earnings/{earning_id}/clawback",
          dependencies=[Depends(management_auth)])
def clawback_earning(tenant_id: UUID, earning_id: UUID, payload: ClawbackIn,
                     request: Request = None, session: Session = Depends(db)):
    def fn():
        row = commission_service.clawback_earning(
            session, _tid(tenant_id), earning_id, amount=payload.amount, kind=payload.kind,
            source_event_id=payload.source_event_id, reason=payload.reason, actor=_actor(request))
        return {"id": str(row.id), "amount": row.amount, "kind": row.kind}
    return _run(session, fn, request)


@app.post("/api/tenancy/tenants/{tenant_id}/commission-earnings/{earning_id}/adjust",
          dependencies=[Depends(management_auth)])
def adjust_earning(tenant_id: UUID, earning_id: UUID, payload: AdjustmentIn,
                   request: Request = None, session: Session = Depends(db)):
    def fn():
        row = commission_service.adjust_earning(session, _tid(tenant_id), earning_id, amount=payload.amount,
                                                kind=payload.kind, reason=payload.reason,
                                                actor=_actor(request))
        return {"id": str(row.id), "amount": row.amount}
    return _run(session, fn, request)


@app.get("/api/tenancy/tenants/{tenant_id}/commission-earnings", dependencies=[Depends(management_auth)])
def list_earnings(tenant_id: UUID, session: Session = Depends(db)):
    from ..models import CommissionEarning

    rows = list(session.scalars(select(CommissionEarning).where(
        CommissionEarning.tenant_id == _tid(tenant_id)).order_by(CommissionEarning.created_at.desc()).limit(200)))
    return [{"id": str(e.id), "partner_id": str(e.partner_id), "basis": e.basis, "amount": e.amount,
             "status": e.status, "formula": e.rate_formula, "source_event_id": e.source_event_id} for e in rows]


# ===========================================================================
# Settlements
# ===========================================================================
@app.post("/api/tenancy/tenants/{tenant_id}/settlement-cycles", status_code=status.HTTP_201_CREATED,
          dependencies=[Depends(management_auth)])
def create_cycle(tenant_id: UUID, payload: CycleIn, request: Request = None, session: Session = Depends(db)):
    def fn():
        row = settlement_service.create_cycle(
            session, _tid(tenant_id), code=payload.code,
            period_start=datetime.fromisoformat(payload.period_start),
            period_end=datetime.fromisoformat(payload.period_end), currency=payload.currency)
        return {"id": str(row.id), "code": row.code}
    return _run(session, fn, request)


@app.post("/api/tenancy/tenants/{tenant_id}/settlements", status_code=status.HTTP_201_CREATED,
          dependencies=[Depends(management_auth)])
def create_settlement(tenant_id: UUID, payload: SettlementIn, request: Request = None,
                      session: Session = Depends(db)):
    def fn():
        row = settlement_service.create_settlement(session, _tid(tenant_id), partner_id=UUID(payload.partner_id),
                                                   cycle_id=UUID(payload.cycle_id), currency=payload.currency)
        return {"id": str(row.id), "state": row.state}
    return _run(session, fn, request)


@app.post("/api/tenancy/tenants/{tenant_id}/settlements/{settlement_id}/calculate", dependencies=[Depends(management_auth)])
def calculate_settlement(tenant_id: UUID, settlement_id: UUID, request: Request = None,
                         session: Session = Depends(db)):
    def fn():
        row = settlement_service.calculate_settlement(session, _tid(tenant_id), settlement_id,
                                                      actor=_actor(request))
        return {"id": str(row.id), "state": row.state, "net": row.net_settlement,
                "earnings": row.total_earnings, "clawbacks": row.total_clawbacks}
    return _run(session, fn, request)


@app.post("/api/tenancy/tenants/{tenant_id}/settlements/{settlement_id}/review", dependencies=[Depends(management_auth)])
def review_settlement(tenant_id: UUID, settlement_id: UUID, request: Request = None,
                      session: Session = Depends(db)):
    def fn():
        row = settlement_service.submit_for_review(session, _tid(tenant_id), settlement_id, actor=_actor(request))
        return {"id": str(row.id), "state": row.state}
    return _run(session, fn, request)


@app.post("/api/tenancy/tenants/{tenant_id}/settlements/{settlement_id}/approve", dependencies=[Depends(management_auth)])
def approve_settlement(tenant_id: UUID, settlement_id: UUID, request: Request = None,
                       session: Session = Depends(db)):
    def fn():
        row = settlement_service.approve_settlement(session, _tid(tenant_id), settlement_id,
                                                    approved_by=_actor(request))
        return {"id": str(row.id), "state": row.state}
    return _run(session, fn, request)


@app.post("/api/tenancy/tenants/{tenant_id}/settlements/{settlement_id}/lock", dependencies=[Depends(management_auth)])
def lock_settlement(tenant_id: UUID, settlement_id: UUID, request: Request = None,
                    session: Session = Depends(db)):
    def fn():
        row = settlement_service.lock_settlement(session, _tid(tenant_id), settlement_id, actor=_actor(request))
        return {"id": str(row.id), "state": row.state}
    return _run(session, fn, request)


@app.post("/api/tenancy/tenants/{tenant_id}/settlements/{settlement_id}/statement", dependencies=[Depends(management_auth)])
def generate_statement(tenant_id: UUID, settlement_id: UUID, request: Request = None,
                       session: Session = Depends(db)):
    def fn():
        row = settlement_service.generate_statement(session, _tid(tenant_id), settlement_id, actor=_actor(request))
        return {"id": str(row.id), "data": row.statement_data}
    return _run(session, fn, request)


@app.post("/api/tenancy/tenants/{tenant_id}/settlements/{settlement_id}/payout", dependencies=[Depends(management_auth)])
def record_payout(tenant_id: UUID, settlement_id: UUID, payload: PayoutIn,
                  request: Request = None, session: Session = Depends(db)):
    def fn():
        row = settlement_service.record_payout(session, _tid(tenant_id), settlement_id, amount=payload.amount,
                                               method=payload.method, reference=payload.reference,
                                               recorded_by=payload.recorded_by or _actor(request))
        return {"id": str(row.id), "settlement_id": str(settlement_id), "amount": row.amount}
    return _run(session, fn, request)


@app.post("/api/tenancy/tenants/{tenant_id}/settlements/{settlement_id}/reconcile", dependencies=[Depends(management_auth)])
def reconcile_settlement(tenant_id: UUID, settlement_id: UUID, payload: ReconcileIn,
                         request: Request = None, session: Session = Depends(db)):
    def fn():
        row = settlement_service.reconcile_settlement(session, _tid(tenant_id), settlement_id,
                                                      detail=payload.detail,
                                                      reconciled_by=payload.reconciled_by or _actor(request))
        return {"id": str(row.id), "state": row.state}
    return _run(session, fn, request)


@app.post("/api/tenancy/tenants/{tenant_id}/settlements/{settlement_id}/disputes", status_code=status.HTTP_201_CREATED,
          dependencies=[Depends(management_auth)])
def open_dispute(tenant_id: UUID, settlement_id: UUID, payload: DisputeIn,
                 request: Request = None, session: Session = Depends(db)):
    def fn():
        row = settlement_service.open_dispute(
            session, _tid(tenant_id), settlement_id, line_id=UUID(payload.line_id) if payload.line_id else None,
            reason=payload.reason, submitted_by=payload.submitted_by or _actor(request),
            evidence=payload.evidence)
        return {"id": str(row.id), "state": row.state}
    return _run(session, fn, request)


@app.post("/api/tenancy/tenants/{tenant_id}/disputes/{dispute_id}/resolve", dependencies=[Depends(management_auth)])
def resolve_dispute(tenant_id: UUID, dispute_id: UUID, payload: DisputeResolveIn,
                    request: Request = None, session: Session = Depends(db)):
    def fn():
        row = settlement_service.resolve_dispute(session, _tid(tenant_id), dispute_id,
                                                 resolution=payload.resolution,
                                                 adjustment_ref=payload.adjustment_ref,
                                                 response=payload.response,
                                                 resolved_by=payload.resolved_by or _actor(request))
        return {"id": str(row.id), "state": row.state}
    return _run(session, fn, request)


@app.post("/api/tenancy/tenants/{tenant_id}/settlements/{settlement_id}/reverse", dependencies=[Depends(management_auth)])
def reverse_settlement(tenant_id: UUID, settlement_id: UUID, payload: TenantStatusIn,
                       request: Request = None, session: Session = Depends(db)):
    def fn():
        row = settlement_service.reverse_settlement(session, _tid(tenant_id), settlement_id,
                                                    reason=payload.reason, reversed_by=_actor(request))
        return {"id": str(row.id), "state": row.state}
    return _run(session, fn, request)


@app.get("/api/tenancy/tenants/{tenant_id}/settlements", dependencies=[Depends(management_auth)])
def list_settlements(tenant_id: UUID, session: Session = Depends(db)):
    from ..models import PartnerSettlement

    rows = list(session.scalars(select(PartnerSettlement).where(
        PartnerSettlement.tenant_id == _tid(tenant_id)).order_by(PartnerSettlement.created_at.desc()).limit(200)))
    return [{"id": str(s.id), "partner_id": str(s.partner_id), "state": s.state,
             "net": s.net_settlement, "currency": s.currency} for s in rows]


# ===========================================================================
# Wallets
# ===========================================================================
@app.get("/api/tenancy/tenants/{tenant_id}/partners/{partner_id}/wallet", dependencies=[Depends(management_auth)])
def get_wallet(tenant_id: UUID, partner_id: UUID, session: Session = Depends(db)):
    tid = _tid(tenant_id)
    wallet = wallet_service.ensure_wallet(session, tid, partner_id)
    return {"id": str(wallet.id), "partner_id": str(partner_id), "currency": wallet.currency,
            "balance": wallet.balance}


@app.post("/api/tenancy/tenants/{tenant_id}/wallets/{wallet_id}/entries", status_code=status.HTTP_201_CREATED,
          dependencies=[Depends(management_auth)])
def post_wallet_entry(tenant_id: UUID, wallet_id: UUID, payload: WalletIn,
                      request: Request = None, session: Session = Depends(db)):
    def fn():
        row = wallet_service.post_wallet_entry(
            session, _tid(tenant_id), wallet_id, entry_type=payload.entry_type, amount=payload.amount,
            reference=payload.reference, reason=payload.reason, actor=payload.actor or _actor(request))
        return {"id": str(row.id), "entry_type": row.entry_type, "amount": row.amount,
                "balance_after": row.balance_after}
    return _run(session, fn, request)


# ===========================================================================
# Reports
# ===========================================================================
@app.post("/api/tenancy/tenants/{tenant_id}/reports", status_code=status.HTTP_201_CREATED,
          dependencies=[Depends(management_auth)])
def generate_report(tenant_id: UUID, payload: ReportIn, request: Request = None,
                    session: Session = Depends(db)):
    from .security import current_tenant

    ctx = current_tenant.get()
    if ctx is not None and ctx.scope_kind == "PLATFORM_AGGREGATE":
        raise HTTPException(403, "platform aggregate scope cannot generate tenant reports")
    if ctx is not None and payload.scope_kind not in ("TENANT", "FRANCHISE", "BRANCH", "ORG_UNIT"):
        raise HTTPException(403, "report scope exceeds authorization scope")
    def fn():
        row = report_service.generate_tenant_report(
            session, _tid(tenant_id), report_type=payload.report_type, scope_kind=payload.scope_kind,
            scope_id=UUID(payload.scope_id) if payload.scope_id else None,
            period_start=datetime.fromisoformat(payload.period_start) if payload.period_start else None,
            period_end=datetime.fromisoformat(payload.period_end) if payload.period_end else None,
            generated_by=_actor(request))
        return {"id": str(row.id), "metrics": row.metrics}
    return _run(session, fn, request)


@app.get("/api/tenancy/reports/aggregate", dependencies=[Depends(management_auth)])
def platform_aggregate(payload: AggregateIn = Depends(), request: Request = None,
                       session: Session = Depends(db)):
    from .security import current_tenant

    ctx = current_tenant.get()
    if ctx is None or ctx.scope_kind != "PLATFORM_AGGREGATE":
        raise HTTPException(403, "platform aggregate access requires platform scope")
    return report_service.platform_aggregate(session, metric=payload.metric, period_key=payload.period_key,
                                             dimension=payload.dimension, requested_by=_actor(request))


@app.post("/api/tenancy/tenants/{tenant_id}/reports/exports", status_code=status.HTTP_202_ACCEPTED,
          dependencies=[Depends(management_auth)])
def request_export(tenant_id: UUID, payload: ExportIn, request: Request = None,
                   session: Session = Depends(db)):
    def fn():
        row = report_service.request_export(
            session, _tid(tenant_id), export_type=payload.export_type, scope_kind=payload.scope_kind,
            scope_id=UUID(payload.scope_id) if payload.scope_id else None, requested_by=_actor(request))
        return {"id": str(row.id), "state": row.state}
    return _run(session, fn, request)


# ===========================================================================
# Audit
# ===========================================================================
@app.get("/api/tenancy/audit", dependencies=[Depends(management_auth)])
def audit_log(tenant_id: UUID | None = Query(default=None), limit: int = Query(default=100, le=500),
              session: Session = Depends(db)):
    stmt = select(AuditLog).order_by(AuditLog.occurred_at.desc()).limit(limit)
    if tenant_id is not None:
        stmt = stmt.where(AuditLog.tenant_id == _tid(tenant_id))
    rows = list(session.scalars(stmt))
    return [{"id": str(r.id), "tenant_id": str(r.tenant_id) if r.tenant_id else None,
             "action": r.action, "actor": r.actor, "resource_type": r.resource_type,
             "resource_id": r.resource_id, "reason": r.reason, "correlation_id": r.correlation_id,
             "occurred_at": r.occurred_at.isoformat() if r.occurred_at else None} for r in rows]


# ===========================================================================
# Governance — messaging & campaigns (Batch 4: 514, 518, 520, 523, 525, 543)
# ===========================================================================

@app.post("/api/tenancy/governance/notifications", status_code=201, dependencies=[Depends(management_auth)])
def create_notification(payload: dict, request: Request, session: Session = Depends(db)):
    n = NotificationService.create(session, _tid(None), payload)
    return {"id": str(n.id), "recipient": n.recipient, "channel": n.channel, "status": n.status}


@app.post("/api/tenancy/governance/notifications/{notif_id}/retry", dependencies=[Depends(management_auth)])
def retry_notification(notif_id: UUID, request: Request, session: Session = Depends(db)):
    try:
        n = NotificationService.retry(session, _tid(None), notif_id)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": str(n.id), "status": n.status, "attempts": n.attempts}


@app.post("/api/tenancy/governance/notifications/{notif_id}/deliver", dependencies=[Depends(management_auth)])
def deliver_notification(notif_id: UUID, request: Request, session: Session = Depends(db)):
    try:
        n = NotificationService.deliver(session, _tid(None), notif_id)
    except KeyError as e:
        raise HTTPException(404, str(e))
    return {"id": str(n.id), "status": n.status, "delivered_at": n.delivered_at}


@app.get("/api/tenancy/governance/notifications/delivery/{notif_id}", dependencies=[Depends(management_auth)])
def delivery_status(notif_id: UUID, session: Session = Depends(db)):
    n = session.get(models.Notification, notif_id)
    if n is None or n.tenant_id != _tid(None):
        raise HTTPException(404, "notification not found")
    return {"id": str(n.id), "status": n.status, "sent_at": n.sent_at, "delivered_at": n.delivered_at,
            "read_at": n.read_at, "attempts": n.attempts}


@app.post("/api/tenancy/governance/campaigns", status_code=201, dependencies=[Depends(management_auth)])
def create_campaign(payload: dict, request: Request, session: Session = Depends(db)):
    c = CampaignService.create(session, _tid(None), payload)
    return {"id": str(c.id), "name": c.name, "status": c.status}


@app.post("/api/tenancy/governance/campaigns/{campaign_id}/schedule", dependencies=[Depends(management_auth)])
def schedule_campaign(campaign_id: UUID, payload: dict, request: Request, session: Session = Depends(db)):
    from datetime import datetime as _dt
    try:
        c = CampaignService.schedule(session, _tid(None), campaign_id,
                                     _dt.fromisoformat(payload.get("schedule_at").replace("Z", "+00:00")))
    except KeyError as e:
        raise HTTPException(404, str(e))
    return {"id": str(c.id), "status": c.status, "schedule_at": c.schedule_at}


@app.post("/api/tenancy/governance/campaigns/{campaign_id}/execute", dependencies=[Depends(management_auth)])
def execute_campaign(campaign_id: UUID, request: Request, session: Session = Depends(db)):
    try:
        c = CampaignService.execute(session, _tid(None), campaign_id)
    except KeyError as e:
        raise HTTPException(404, str(e))
    return {"id": str(c.id), "status": c.status, "audience_size": len(c.audience or []),
            "executed_at": c.executed_at}


@app.post("/api/tenancy/governance/campaigns/{campaign_id}/track", dependencies=[Depends(management_auth)])
def track_campaign(campaign_id: UUID, payload: dict, request: Request, session: Session = Depends(db)):
    try:
        r = CampaignService.track(session, _tid(None), campaign_id, payload.get("recipient"),
                                  payload.get("event", "OPENED"))
    except KeyError as e:
        raise HTTPException(404, str(e))
    return {"recipient": r.recipient, "status": r.status, "converted_at": r.converted_at}


@app.get("/api/tenancy/governance/campaigns/{campaign_id}/analytics", dependencies=[Depends(management_auth)])
def campaign_analytics(campaign_id: UUID, session: Session = Depends(db)):
    m = CampaignService.analytics(session, _tid(None), campaign_id)
    return {"campaign_id": str(campaign_id), "sent": m.sent_count, "opened": m.opened_count,
            "clicked": m.clicked_count, "converted": m.converted_count,
            "open_rate": m.open_rate, "conversion_rate": m.conversion_rate}


# ===========================================================================
# Governance — usage, cost, policy, compliance (Batch 4: 759, 760, 776, 779, 1389)
# ===========================================================================

@app.post("/api/tenancy/governance/usage-meter", status_code=201, dependencies=[Depends(management_auth)])
def record_usage(payload: dict, request: Request, session: Session = Depends(db)):
    m = GovernanceService.record_usage(session, _tid(None), payload)
    return {"id": str(m.id), "resource": m.resource, "amount": m.amount, "unit": m.unit}


@app.get("/api/tenancy/governance/usage-meter", dependencies=[Depends(management_auth)])
def list_usage(resource: str | None = None, session: Session = Depends(db)):
    tid = _tid(None)
    stmt = select(models.UsageMeter).where(models.UsageMeter.tenant_id == tid)
    if resource:
        stmt = stmt.where(models.UsageMeter.resource == resource)
    return [{"id": str(m.id), "resource": m.resource, "amount": m.amount, "unit": m.unit,
             "period": m.period, "recorded_at": m.recorded_at} for m in session.scalars(stmt.limit(200))]


@app.post("/api/tenancy/governance/costs", status_code=201, dependencies=[Depends(management_auth)])
def record_cost(payload: dict, request: Request, session: Session = Depends(db)):
    c = GovernanceService.record_cost(session, _tid(None), payload)
    return {"id": str(c.id), "category": c.category, "amount": c.amount, "currency": c.currency}


@app.post("/api/tenancy/governance/costs/optimize", dependencies=[Depends(management_auth)])
def optimize_costs(request: Request, session: Session = Depends(db)):
    return GovernanceService.optimize_costs(session, _tid(None))


@app.post("/api/tenancy/governance/policies", status_code=201, dependencies=[Depends(management_auth)])
def create_policy(payload: dict, request: Request, session: Session = Depends(db)):
    p = GovernanceService.create_policy(session, _tid(None), payload)
    return {"id": str(p.id), "name": p.name, "severity": p.severity, "enabled": p.enabled}


@app.post("/api/tenancy/governance/policies/{policy_id}/evaluate", dependencies=[Depends(management_auth)])
def evaluate_policy(policy_id: UUID, payload: dict, request: Request, session: Session = Depends(db)):
    try:
        return GovernanceService.evaluate_policy(session, _tid(None), policy_id, payload.get("sample", {}))
    except KeyError as e:
        raise HTTPException(404, str(e))


@app.post("/api/tenancy/governance/compliance/run", dependencies=[Depends(management_auth)])
def run_compliance(payload: dict, request: Request, session: Session = Depends(db)):
    c = GovernanceService.run_compliance(session, _tid(None), payload.get("check_name", "auto-scan"))
    return {"id": str(c.id), "check_name": c.check_name, "status": c.status, "result": c.result}


# ===========================================================================
# Governance — security, ecosystem, intelligence (Batch 4: 782, 831, 750, 920, 929)
# ===========================================================================

@app.post("/api/tenancy/governance/threat-hunts", status_code=201, dependencies=[Depends(management_auth)])
def start_threat_hunt(payload: dict, request: Request, session: Session = Depends(db)):
    h = GovernanceService.start_threat_hunt(session, _tid(None), payload)
    return {"id": str(h.id), "name": h.name, "status": h.status}


@app.post("/api/tenancy/governance/threat-hunts/{hunt_id}/complete", dependencies=[Depends(management_auth)])
def complete_threat_hunt(hunt_id: UUID, payload: dict, request: Request, session: Session = Depends(db)):
    try:
        h = GovernanceService.complete_threat_hunt(session, _tid(None), hunt_id, payload.get("findings", []))
    except KeyError as e:
        raise HTTPException(404, str(e))
    return {"id": str(h.id), "status": h.status, "findings": len(h.findings)}


@app.get("/api/tenancy/governance/threat-hunts", dependencies=[Depends(management_auth)])
def list_threat_hunts(session: Session = Depends(db)):
    rows = session.scalars(select(models.ThreatHunt).where(
        models.ThreatHunt.tenant_id == _tid(None)).order_by(models.ThreatHunt.started_at.desc())).all()
    return [{"id": str(h.id), "name": h.name, "status": h.status, "findings": len(h.findings),
             "started_at": h.started_at, "completed_at": h.completed_at} for h in rows]


@app.post("/api/tenancy/governance/service-chains", status_code=201, dependencies=[Depends(management_auth)])
def create_service_chain(payload: dict, request: Request, session: Session = Depends(db)):
    c = GovernanceService.create_chain(session, _tid(None), payload)
    return {"id": str(c.id), "name": c.name, "steps": len(c.services or []), "status": c.status}


@app.get("/api/tenancy/governance/service-chains", dependencies=[Depends(management_auth)])
def list_service_chains(session: Session = Depends(db)):
    rows = session.scalars(select(models.ServiceChain).where(
        models.ServiceChain.tenant_id == _tid(None))).all()
    return [{"id": str(c.id), "name": c.name, "services": c.services, "status": c.status} for c in rows]


@app.post("/api/tenancy/governance/insights", status_code=201, dependencies=[Depends(management_auth)])
def create_insight(payload: dict, request: Request, session: Session = Depends(db)):
    i = GovernanceService.create_insight(session, _tid(None), payload)
    return {"id": str(i.id), "kind": i.kind, "title": i.title, "confidence": i.confidence}


@app.get("/api/tenancy/governance/insights", dependencies=[Depends(management_auth)])
def list_insights(kind: str | None = None, session: Session = Depends(db)):
    stmt = select(models.Insight).where(models.Insight.tenant_id == _tid(None))
    if kind:
        stmt = stmt.where(models.Insight.kind == kind)
    rows = session.scalars(stmt.order_by(models.Insight.generated_at.desc()).limit(100)).all()
    return [{"id": str(i.id), "kind": i.kind, "title": i.title, "body": i.body,
             "confidence": i.confidence} for i in rows]


@app.post("/api/tenancy/governance/knowledge-docs", status_code=201, dependencies=[Depends(management_auth)])
def index_doc(payload: dict, request: Request, session: Session = Depends(db)):
    d = GovernanceService.index_doc(session, _tid(None), payload)
    return {"id": str(d.id), "title": d.title, "tags": d.tags}


@app.get("/api/tenancy/governance/knowledge-docs/search", dependencies=[Depends(management_auth)])
def search_docs(q: str, session: Session = Depends(db)):
    return GovernanceService.search_docs(session, _tid(None), q)


# ===========================================================================
# Governance — enterprise ops, ROI, infra control (Batch 4: 924, 926, 948, 630, 638, 639, 752, 754, 892)
# ===========================================================================

@app.post("/api/tenancy/governance/procurement", status_code=201, dependencies=[Depends(management_auth)])
def create_procurement(payload: dict, request: Request, session: Session = Depends(db)):
    p = GovernanceService.create_procurement(session, _tid(None), payload)
    return {"id": str(p.id), "item": p.item, "quantity": p.quantity, "status": p.status}


@app.post("/api/tenancy/governance/inventory/forecast", status_code=201, dependencies=[Depends(management_auth)])
def forecast_inventory(payload: dict, request: Request, session: Session = Depends(db)):
    f = GovernanceService.forecast_inventory(session, _tid(None), payload)
    return {"id": str(f.id), "item": f.item, "predicted_demand": f.predicted_demand,
            "confidence": f.confidence}


@app.post("/api/tenancy/governance/roi", status_code=201, dependencies=[Depends(management_auth)])
def record_roi(payload: dict, request: Request, session: Session = Depends(db)):
    r = GovernanceService.record_roi(session, _tid(None), payload)
    return {"id": str(r.id), "project": r.project, "roi_pct": r.roi_pct}


@app.get("/api/tenancy/governance/roi", dependencies=[Depends(management_auth)])
def list_roi(session: Session = Depends(db)):
    rows = session.scalars(select(models.RoiRecord).where(
        models.RoiRecord.tenant_id == _tid(None)).order_by(models.RoiRecord.roi_pct.desc())).all()
    return [{"id": str(r.id), "project": r.project, "investment": r.investment,
             "return_value": r.return_value, "roi_pct": r.roi_pct} for r in rows]


@app.post("/api/tenancy/governance/scaling-rules", status_code=201, dependencies=[Depends(management_auth)])
def create_scaling_rule(payload: dict, request: Request, session: Session = Depends(db)):
    s = GovernanceService.create_scaling_rule(session, _tid(None), payload)
    return {"id": str(s.id), "service": s.service, "metric": s.metric, "threshold": s.threshold}


@app.get("/api/tenancy/governance/scaling-rules", dependencies=[Depends(management_auth)])
def list_scaling_rules(session: Session = Depends(db)):
    rows = session.scalars(select(models.ScalingRule).where(
        models.ScalingRule.tenant_id == _tid(None))).all()
    return [{"id": str(r.id), "service": r.service, "metric": r.metric,
             "threshold": r.threshold, "min": r.min_instances, "max": r.max_instances} for r in rows]


@app.post("/api/tenancy/governance/mesh-links", status_code=201, dependencies=[Depends(management_auth)])
def create_mesh_link(payload: dict, request: Request, session: Session = Depends(db)):
    m = GovernanceService.create_mesh_link(session, _tid(None), payload)
    return {"id": str(m.id), "source": m.source, "target": m.target,
            "mtls_enabled": m.mtls_enabled, "status": m.status}


@app.get("/api/tenancy/governance/mesh-links", dependencies=[Depends(management_auth)])
def list_mesh_links(session: Session = Depends(db)):
    rows = session.scalars(select(models.MeshLink).where(
        models.MeshLink.tenant_id == _tid(None))).all()
    return [{"id": str(m.id), "source": m.source, "target": m.target,
             "mtls_enabled": m.mtls_enabled, "status": m.status} for m in rows]


@app.post("/api/tenancy/governance/cloud-providers", status_code=201, dependencies=[Depends(management_auth)])
def register_cloud(payload: dict, request: Request, session: Session = Depends(db)):
    c = GovernanceService.register_cloud(session, _tid(None), payload)
    return {"id": str(c.id), "provider": c.provider, "region": c.region,
            "abstraction_status": c.abstraction_status, "portability_status": c.portability_status}


@app.post("/api/tenancy/governance/workloads/migrate", dependencies=[Depends(management_auth)])
def migrate_workload(payload: dict, request: Request, session: Session = Depends(db)):
    try:
        c = GovernanceService.migrate_workload(session, _tid(None), payload.get("workload_name"),
                                               payload.get("target_cloud"))
    except KeyError as e:
        raise HTTPException(404, str(e))
    return {"workload": c.workload_name, "from": c.source_cloud, "to": c.target_cloud,
            "portability_status": c.portability_status}


@app.post("/api/tenancy/governance/translations", status_code=201, dependencies=[Depends(management_auth)])
def translate(payload: dict, request: Request, session: Session = Depends(db)):
    t = GovernanceService.translate(session, _tid(None), payload.get("text", ""),
                                    payload.get("target_lang", "es"))
    return {"id": str(t.id), "source_text": t.source_text, "translated_text": t.translated_text,
            "target_lang": t.target_lang}


# ---------------------------------------------------------------------------
# Core-platform AI/governance (Batch 8g: 532, 548, 615, 747, 762, 832, 909,
# 910, 918, 925, 935)
# ---------------------------------------------------------------------------

@app.post("/api/tenancy/governance/sentiment", status_code=201, dependencies=[Depends(management_auth)])
def analyze_sentiment(payload: dict, request: Request, session: Session = Depends(db)):
    row = CoreAiService.analyze_sentiment(session, _tid(None), payload)
    return {"id": str(row.id), "sentiment": row.sentiment, "score": row.score}


@app.get("/api/tenancy/governance/sentiment", dependencies=[Depends(management_auth)])
def list_sentiment(request: Request, session: Session = Depends(db)):
    rows = session.scalars(select(models.SentimentAnalysis).where(
        models.SentimentAnalysis.tenant_id == _tid(None)).order_by(
        models.SentimentAnalysis.created_at.desc()).limit(100)).all()
    return [{"id": str(r.id), "text": r.text[:60], "sentiment": r.sentiment, "score": r.score}
            for r in rows]


@app.post("/api/tenancy/governance/smart-reply", status_code=201, dependencies=[Depends(management_auth)])
def suggest_reply(payload: dict, request: Request, session: Session = Depends(db)):
    row = CoreAiService.suggest_reply(session, _tid(None), payload)
    return {"id": str(row.id), "suggested_reply": row.suggested_reply}


@app.post("/api/tenancy/governance/consensus/elect", status_code=201, dependencies=[Depends(management_auth)])
def elect_leader(payload: dict, request: Request, session: Session = Depends(db)):
    row = CoreAiService.elect_leader(session, _tid(None), payload)
    return {"id": str(row.id), "cluster": row.cluster, "node_id": row.node_id,
            "term": row.term, "votes": row.votes, "is_leader": row.is_leader}


@app.post("/api/tenancy/governance/beta-rollouts", status_code=201, dependencies=[Depends(management_auth)])
def release_beta(payload: dict, request: Request, session: Session = Depends(db)):
    row = CoreAiService.release_beta(session, _tid(None), payload)
    return {"id": str(row.id), "feature": row.feature, "version": row.version,
            "cohort_pct": row.cohort_pct, "status": row.status}


@app.post("/api/tenancy/governance/carbon", status_code=201, dependencies=[Depends(management_auth)])
def calculate_carbon(payload: dict, request: Request, session: Session = Depends(db)):
    row = CoreAiService.calculate_carbon(session, _tid(None), payload)
    return {"id": str(row.id), "scope": row.scope, "co2_kg": row.co2_kg, "period": row.period}


@app.post("/api/tenancy/governance/intents", status_code=201, dependencies=[Depends(management_auth)])
def execute_intent(payload: dict, request: Request, session: Session = Depends(db)):
    row = CoreAiService.execute_intent(session, _tid(None), payload)
    return {"id": str(row.id), "intent": row.intent, "action": row.action, "status": row.status}


@app.post("/api/tenancy/governance/clauses/extract", status_code=201, dependencies=[Depends(management_auth)])
def extract_clause(payload: dict, request: Request, session: Session = Depends(db)):
    row = CoreAiService.extract_clause(session, _tid(None), payload)
    return {"id": str(row.id), "document_id": row.document_id, "clause_type": row.clause_type}


@app.post("/api/tenancy/governance/risk", status_code=201, dependencies=[Depends(management_auth)])
def assess_risk(payload: dict, request: Request, session: Session = Depends(db)):
    row = CoreAiService.assess_risk(session, _tid(None), payload)
    return {"id": str(row.id), "entity": row.entity, "entity_id": row.entity_id,
            "risk_level": row.risk_level, "score": row.score}


@app.get("/api/tenancy/governance/risk", dependencies=[Depends(management_auth)])
def list_risks(entity: str | None = None, request: Request = None, session: Session = Depends(db)):
    stmt = select(models.RiskAssessment).where(models.RiskAssessment.tenant_id == _tid(None))
    if entity:
        stmt = stmt.where(models.RiskAssessment.entity == entity)
    rows = session.scalars(stmt.order_by(models.RiskAssessment.created_at.desc()).limit(100)).all()
    return [{"id": str(r.id), "entity": r.entity, "entity_id": r.entity_id,
             "risk_level": r.risk_level, "score": r.score} for r in rows]


@app.post("/api/tenancy/governance/strategy", status_code=201, dependencies=[Depends(management_auth)])
def suggest_strategy(payload: dict, request: Request, session: Session = Depends(db)):
    row = CoreAiService.suggest_strategy(session, _tid(None), payload)
    return {"id": str(row.id), "objective": row.objective, "recommendation": row.recommendation}


@app.post("/api/tenancy/governance/ethics", status_code=201, dependencies=[Depends(management_auth)])
def validate_ethics(payload: dict, request: Request, session: Session = Depends(db)):
    row = CoreAiService.validate_ethics(session, _tid(None), payload)
    return {"id": str(row.id), "decision": row.decision, "ethical": row.ethical,
            "reason": row.reason}


# ---------------------------------------------------------------------------
# Lab / testing simulators (1101 OLT, 1106 Latency)
# ---------------------------------------------------------------------------

@app.post("/api/tenancy/governance/lab/olt-simulators", status_code=201, dependencies=[Depends(management_auth)])
def create_olt_simulator(payload: dict, request: Request, session: Session = Depends(db)):
    sim = LabService.create_olt_simulator(session, _tid(None), payload)
    return {"id": str(sim.id), "sim_name": sim.sim_name, "pon_type": sim.pon_type,
            "onu_count": sim.onu_count, "status": sim.status}


@app.get("/api/tenancy/governance/lab/olt-simulators", dependencies=[Depends(management_auth)])
def list_olt_simulators(request: Request, session: Session = Depends(db)):
    rows = session.scalars(select(models.OltSimulator).where(
        models.OltSimulator.tenant_id == _tid(None))).all()
    return [{"id": str(r.id), "sim_name": r.sim_name, "pon_type": r.pon_type,
             "onu_count": r.onu_count, "uptime_pct": r.uptime_pct, "status": r.status}
            for r in rows]


@app.post("/api/tenancy/governance/lab/olt-simulators/{sim_id}/run", dependencies=[Depends(management_auth)])
def run_olt_simulator(sim_id: uuid.UUID, request: Request, session: Session = Depends(db)):
    try:
        sim = LabService.run_olt_simulator(session, _tid(None), sim_id)
    except KeyError:
        raise HTTPException(404, "OLT simulator not found")
    return {"id": str(sim.id), "sim_name": sim.sim_name, "status": sim.status,
            "uptime_pct": sim.uptime_pct}


@app.post("/api/tenancy/governance/lab/latency-emulators", status_code=201, dependencies=[Depends(management_auth)])
def create_latency_simulator(payload: dict, request: Request, session: Session = Depends(db)):
    sim = LabService.create_latency_simulator(session, _tid(None), payload)
    return {"id": str(sim.id), "sim_name": sim.sim_name, "scenario": sim.scenario,
            "base_latency_ms": sim.base_latency_ms, "status": sim.status}


@app.get("/api/tenancy/governance/lab/latency-emulators", dependencies=[Depends(management_auth)])
def list_latency_simulators(request: Request, session: Session = Depends(db)):
    rows = session.scalars(select(models.LatencySimulator).where(
        models.LatencySimulator.tenant_id == _tid(None))).all()
    return [{"id": str(r.id), "sim_name": r.sim_name, "scenario": r.scenario,
             "base_latency_ms": r.base_latency_ms, "jitter_ms": r.jitter_ms,
             "packet_loss_pct": r.packet_loss_pct, "status": r.status} for r in rows]


@app.post("/api/tenancy/governance/lab/latency-emulators/{sim_id}/simulate", dependencies=[Depends(management_auth)])
def simulate_latency(sim_id: uuid.UUID, request: Request, session: Session = Depends(db)):
    try:
        sim = LabService.simulate_latency(session, _tid(None), sim_id)
    except KeyError:
        raise HTTPException(404, "Latency simulator not found")
    return {"id": str(sim.id), "sim_name": sim.sim_name, "scenario": sim.scenario,
            "status": sim.status}
