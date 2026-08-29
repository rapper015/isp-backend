"""Organization hierarchy, partners, agreements, customer ownership, transfers
and data-access grants. Hierarchy uses validated materialized paths."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.exceptions import (
    CircularHierarchyError,
    DuplicateError,
    NotFoundError,
    PartnerNotActiveError,
    ScopeExpansionError,
    ValidationError,
)
from ..domain.identity import is_descendant, validate_hierarchy_path
from ..events import outbox
from ..models import (
    CustomerOwnership,
    CustomerTransfer,
    DataAccessGrant,
    OrganizationUnit,
    OrganizationUnitHistory,
    OwnershipHistory,
    Partner,
    PartnerAgreement,
    PartnerAgreementVersion,
    PartnerMembership,
    PartnerRelationship,
    PartnerServiceScope,
    PartnerStatusHistory,
    PartnerTerritory,
)
from ..state_machine import guarded, partner_transition, transfer_transition
from .audit_service import audit, correlation

PARTNER_TYPES = ("FRANCHISE", "RESELLER", "DISTRIBUTOR", "MANAGED_OPERATOR",
                 "COLLECTION_PARTNER", "FIELD_SERVICE_PARTNER", "NETWORK_PARTNER", "OTHER")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_partner_or_404(session: Session, tenant_id, partner_id) -> Partner:
    partner = session.get(Partner, partner_id)
    if partner is None or partner.tenant_id != tenant_id:
        raise NotFoundError("partner not found")
    return partner


def get_org_unit_or_404(session: Session, tenant_id, org_unit_id) -> OrganizationUnit:
    unit = session.get(OrganizationUnit, org_unit_id)
    if unit is None or unit.tenant_id != tenant_id:
        raise NotFoundError("organization unit not found")
    return unit


# ---------------------------------------------------------------------------
# Organization units (branch / team / legal entity)
# ---------------------------------------------------------------------------
def create_org_unit(session: Session, tenant_id, *, unit_type: str, code: str, name: str,
                    parent_id: uuid.UUID | None = None, actor: str = "system",
                    correlation_id: str | None = None) -> OrganizationUnit:
    request_id = correlation(correlation_id)
    parent_path = None
    if parent_id is not None:
        parent = get_org_unit_or_404(session, tenant_id, parent_id)
        parent_path = parent.path
    path = validate_hierarchy_path(parent_path, tenant_id, tenant_id, parent_id)
    existing = session.scalars(select(OrganizationUnit).where(
        OrganizationUnit.tenant_id == tenant_id, OrganizationUnit.code == code)).first()
    if existing is not None:
        raise DuplicateError(f"organization unit code {code!r} already exists")
    unit = OrganizationUnit(tenant_id=tenant_id, parent_id=parent_id, unit_type=unit_type,
                            code=code, name=name, path=path)
    session.add(unit)
    session.flush()
    session.add(OrganizationUnitHistory(tenant_id=tenant_id, org_unit_id=unit.id, change_type="CREATED",
                                        before={}, after={"name": name, "path": path}, changed_by=actor,
                                        correlation_id=request_id))
    audit(session, tenant_id, actor, "org_unit.created", resource_type="organization_unit",
          resource_id=unit.id, after={"code": code, "type": unit_type, "path": path},
          correlation_id=request_id)
    return unit


def reparent_org_unit(session: Session, tenant_id, org_unit_id, *, new_parent_id: uuid.UUID | None,
                      actor: str = "system") -> OrganizationUnit:
    unit = get_org_unit_or_404(session, tenant_id, org_unit_id)
    if new_parent_id is not None:
        parent = get_org_unit_or_404(session, tenant_id, new_parent_id)
        new_path = validate_hierarchy_path(parent.path, tenant_id, tenant_id, new_parent_id)
        if unit.id == new_parent_id or is_descendant(parent.path, unit.path):
            raise CircularHierarchyError("reparenting would create a cycle")
    else:
        new_path = str(tenant_id)
    old_path = unit.path
    unit.parent_id = new_parent_id
    unit.path = new_path
    session.flush()
    # Fix descendant paths.
    for desc in session.scalars(select(OrganizationUnit).where(
            OrganizationUnit.tenant_id == tenant_id, OrganizationUnit.path.like(old_path + "%"),
            OrganizationUnit.id != unit.id)):
        desc.path = new_path + desc.path[len(old_path):]
    session.add(OrganizationUnitHistory(tenant_id=tenant_id, org_unit_id=unit.id, change_type="REPARENTED",
                                        before={"path": old_path}, after={"path": new_path},
                                        changed_by=actor))
    audit(session, tenant_id, actor, "org_unit.reparented", resource_type="organization_unit",
          resource_id=unit.id, before={"path": old_path}, after={"path": new_path})
    return unit


def list_org_units(session: Session, tenant_id, *, parent_id: uuid.UUID | None = None) -> list[OrganizationUnit]:
    stmt = select(OrganizationUnit).where(OrganizationUnit.tenant_id == tenant_id)
    if parent_id is not None:
        parent = get_org_unit_or_404(session, tenant_id, parent_id)
        stmt = stmt.where(OrganizationUnit.path.like(parent.path + "/%"))
    return list(session.scalars(stmt.order_by(OrganizationUnit.path)))


# ---------------------------------------------------------------------------
# Partners
# ---------------------------------------------------------------------------
def create_partner(session: Session, tenant_id, *, partner_type: str, code: str, name: str,
                   org_unit_id: uuid.UUID | None = None, contact_person: str | None = None,
                   email: str | None = None, phone: str | None = None, currency: str = "INR",
                   actor: str = "system", correlation_id: str | None = None) -> Partner:
    request_id = correlation(correlation_id)
    if partner_type not in PARTNER_TYPES:
        raise ValidationError(f"invalid partner type {partner_type!r}")
    if session.scalars(select(Partner).where(Partner.tenant_id == tenant_id,
                                             Partner.code == code)).first() is not None:
        raise DuplicateError(f"partner code {code!r} already exists")
    partner = Partner(tenant_id=tenant_id, partner_type=partner_type, code=code, name=name,
                      org_unit_id=org_unit_id, contact_person=contact_person, email=email,
                      phone=phone, currency=currency, status="PROSPECT", correlation_id=request_id)
    session.add(partner)
    session.flush()
    audit(session, tenant_id, actor, "partner.created", resource_type="partner",
          resource_id=partner.id, after={"code": code, "type": partner_type}, correlation_id=request_id)
    outbox(session, "tenancy.partner.created.v1", tenant_id, request_id,
           {"tenant_id": str(tenant_id), "partner_id": str(partner.id), "code": code})
    return partner


def link_partners(session: Session, tenant_id, parent_id: uuid.UUID, child_id: uuid.UUID, *,
                  relationship_type: str = "FRANCHISE_OF", actor: str = "system") -> PartnerRelationship:
    parent = get_partner_or_404(session, tenant_id, parent_id)
    child = get_partner_or_404(session, tenant_id, child_id)
    if parent_id == child_id:
        raise CircularHierarchyError("a partner cannot be its own parent")
    child_path = child.org_unit_id and get_org_unit_or_404(session, tenant_id, child.org_unit_id).path
    parent_path = parent.org_unit_id and get_org_unit_or_404(session, tenant_id, parent.org_unit_id).path
    if child_path and parent_path and is_descendant(child_path, parent_path):
        raise CircularHierarchyError("partner relationship would create a cycle")
    existing = session.scalars(select(PartnerRelationship).where(
        PartnerRelationship.tenant_id == tenant_id, PartnerRelationship.parent_id == parent_id,
        PartnerRelationship.child_id == child_id,
        PartnerRelationship.relationship_type == relationship_type)).first()
    if existing is not None:
        existing.is_active = True
        return existing
    row = PartnerRelationship(tenant_id=tenant_id, parent_id=parent_id, child_id=child_id,
                              relationship_type=relationship_type, path=f"{parent_id}/{child_id}")
    session.add(row)
    session.flush()
    audit(session, tenant_id, actor, "partner.relationship.linked", resource_type="partner",
          resource_id=child_id, after={"parent": str(parent_id), "type": relationship_type})
    return row


def change_partner_status(session: Session, tenant_id, partner_id, *, to_status: str, reason: str | None,
                          actor: str = "system", correlation_id: str | None = None) -> Partner:
    request_id = correlation(correlation_id)
    partner = get_partner_or_404(session, tenant_id, partner_id)
    if partner.status == to_status:
        return partner
    try:
        partner_transition(partner.status, to_status)
    except ValueError as error:
        raise ValidationError(str(error)) from error
    session.add(PartnerStatusHistory(tenant_id=tenant_id, partner_id=partner.id,
                                     from_status=partner.status, to_status=to_status, reason=reason,
                                     changed_by=actor))
    partner.status = to_status
    session.flush()
    audit(session, tenant_id, actor, "partner.status_changed", resource_type="partner",
          resource_id=partner.id, before={"status": partner.status}, after={"status": to_status},
          reason=reason, correlation_id=request_id)
    outbox(session, "tenancy.partner.status_changed.v1", tenant_id, request_id,
           {"tenant_id": str(tenant_id), "partner_id": str(partner.id), "to_status": to_status})
    return partner


def _require_active_partner(partner: Partner) -> None:
    if partner.status not in ("ACTIVE", "RESTRICTED"):
        raise PartnerNotActiveError(f"partner is not active (state {partner.status})")


def create_agreement(session: Session, tenant_id, *, partner_id: uuid.UUID, code: str,
                     customer_ownership_model: str = "TENANT_OWNED",
                     actor: str = "system") -> PartnerAgreement:
    partner = get_partner_or_404(session, tenant_id, partner_id)
    _require_active_partner(partner)
    if session.scalars(select(PartnerAgreement).where(PartnerAgreement.tenant_id == tenant_id,
                                                      PartnerAgreement.code == code)).first() is not None:
        raise DuplicateError(f"agreement code {code!r} already exists")
    agreement = PartnerAgreement(tenant_id=tenant_id, partner_id=partner_id, code=code,
                                 customer_ownership_model=customer_ownership_model)
    session.add(agreement)
    session.flush()
    return agreement


def add_agreement_version(session: Session, tenant_id, agreement_id: uuid.UUID, *, terms: dict,
                          actor: str = "system") -> PartnerAgreementVersion:
    agreement = session.get(PartnerAgreement, agreement_id)
    if agreement is None or agreement.tenant_id != tenant_id:
        raise NotFoundError("agreement not found")
    version = (session.scalar(select(PartnerAgreementVersion.version).where(
        PartnerAgreementVersion.agreement_id == agreement.id).order_by(
        PartnerAgreementVersion.version.desc()).limit(1))) or 0
    row = PartnerAgreementVersion(agreement_id=agreement.id, tenant_id=tenant_id,
                                  version=version + 1, terms=terms, changed_by=actor)
    session.add(row)
    session.flush()
    return row


def add_service_scope(session: Session, tenant_id, partner_id: uuid.UUID, *, service: str,
                      enabled: bool = True, detail: dict | None = None,
                      actor: str = "system") -> PartnerServiceScope:
    get_partner_or_404(session, tenant_id, partner_id)
    row = session.scalars(select(PartnerServiceScope).where(
        PartnerServiceScope.tenant_id == tenant_id, PartnerServiceScope.partner_id == partner_id,
        PartnerServiceScope.service == service)).first()
    if row is None:
        row = PartnerServiceScope(tenant_id=tenant_id, partner_id=partner_id, service=service,
                                  enabled=enabled, detail=detail or {})
        session.add(row)
    else:
        row.enabled = enabled
        row.detail = detail or {}
    session.flush()
    return row


def add_territory(session: Session, tenant_id, partner_id: uuid.UUID, *, territory_key: str,
                  region: str | None = None, is_primary: bool = False,
                  actor: str = "system") -> PartnerTerritory:
    get_partner_or_404(session, tenant_id, partner_id)
    row = session.scalars(select(PartnerTerritory).where(
        PartnerTerritory.tenant_id == tenant_id, PartnerTerritory.partner_id == partner_id,
        PartnerTerritory.territory_key == territory_key)).first()
    if row is None:
        row = PartnerTerritory(tenant_id=tenant_id, partner_id=partner_id, territory_key=territory_key,
                               region=region, is_primary=is_primary)
        session.add(row)
    else:
        row.is_primary = is_primary
        row.region = region
    session.flush()
    return row


def add_partner_membership(session: Session, tenant_id, partner_id: uuid.UUID, *, user_id: str,
                           role: str, granted_by: str = "system") -> PartnerMembership:
    get_partner_or_404(session, tenant_id, partner_id)
    row = session.scalars(select(PartnerMembership).where(
        PartnerMembership.tenant_id == tenant_id, PartnerMembership.partner_id == partner_id,
        PartnerMembership.user_id == user_id)).first()
    if row is None:
        row = PartnerMembership(tenant_id=tenant_id, partner_id=partner_id, user_id=user_id,
                                role=role, granted_by=granted_by)
        session.add(row)
    else:
        row.role = role
        row.status = "ACTIVE"
    session.flush()
    return row


# ---------------------------------------------------------------------------
# Customer / service ownership
# ---------------------------------------------------------------------------
def set_ownership(session: Session, tenant_id, *, customer_id: str,
                  owning_org_unit_id: uuid.UUID | None = None,
                  acquisition_partner_id: uuid.UUID | None = None,
                  servicing_partner_id: uuid.UUID | None = None,
                  billing_owner_id: uuid.UUID | None = None,
                  support_owner_id: uuid.UUID | None = None,
                  network_owner_id: uuid.UUID | None = None,
                  collection_owner_id: uuid.UUID | None = None,
                  actor: str = "system", correlation_id: str | None = None) -> CustomerOwnership:
    request_id = correlation(correlation_id)
    for ref in (acquisition_partner_id, servicing_partner_id, billing_owner_id,
                support_owner_id, network_owner_id, collection_owner_id):
        if ref is not None:
            get_partner_or_404(session, tenant_id, ref)
    if owning_org_unit_id is not None:
        get_org_unit_or_404(session, tenant_id, owning_org_unit_id)
    row = session.scalars(select(CustomerOwnership).where(
        CustomerOwnership.tenant_id == tenant_id,
        CustomerOwnership.customer_id == customer_id)).first()
    before = {}
    if row is None:
        row = CustomerOwnership(tenant_id=tenant_id, customer_id=customer_id)
        session.add(row)
        change_type = "ACQUIRED"
    else:
        before = {"owning": str(row.owning_org_unit_id), "acquisition": str(row.acquisition_partner_id)}
        change_type = "SCOPE_CHANGED"
    row.owning_org_unit_id = owning_org_unit_id
    row.acquisition_partner_id = acquisition_partner_id
    row.servicing_partner_id = servicing_partner_id
    row.billing_owner_id = billing_owner_id
    row.support_owner_id = support_owner_id
    row.network_owner_id = network_owner_id
    row.collection_owner_id = collection_owner_id
    row.owned_at = _now()
    session.flush()
    after = {"owning": str(row.owning_org_unit_id), "acquisition": str(row.acquisition_partner_id),
             "servicing": str(row.servicing_partner_id), "billing": str(row.billing_owner_id),
             "support": str(row.support_owner_id), "network": str(row.network_owner_id),
             "collection": str(row.collection_owner_id)}
    session.add(OwnershipHistory(tenant_id=tenant_id, customer_id=customer_id, change_type=change_type,
                                 before=before, after=after, changed_by=actor, correlation_id=request_id))
    audit(session, tenant_id, actor, "ownership.changed", resource_type="customer", resource_id=customer_id,
          before=before, after=after, correlation_id=request_id)
    outbox(session, "tenancy.ownership.changed.v1", tenant_id, request_id,
           {"tenant_id": str(tenant_id), "customer_id": customer_id, "after": after})
    return row


def transfer_customer(session: Session, tenant_id, *, customer_id: str,
                      to_owner_id: uuid.UUID | None, transfer_type: str = "PARTNER_TO_TENANT",
                      reason: str, requested_by: str = "system",
                      correlation_id: str | None = None) -> CustomerTransfer:
    request_id = correlation(correlation_id)
    ownership = session.scalars(select(CustomerOwnership).where(
        CustomerOwnership.tenant_id == tenant_id,
        CustomerOwnership.customer_id == customer_id)).first()
    if ownership is None:
        raise NotFoundError("customer ownership record not found")
    transfer = CustomerTransfer(tenant_id=tenant_id, customer_id=customer_id,
                                from_owner_id=ownership.owning_org_unit_id, to_owner_id=to_owner_id,
                                transfer_type=transfer_type, state="REQUESTED", reason=reason,
                                requested_by=requested_by,
                                validation=_validate_transfer(session, tenant_id, customer_id))
    session.add(transfer)
    session.flush()
    audit(session, tenant_id, requested_by, "ownership.transfer_requested", resource_type="customer",
          resource_id=customer_id, after={"transfer_id": str(transfer.id), "reason": reason},
          correlation_id=request_id)
    return transfer


def _validate_transfer(session: Session, tenant_id, customer_id: str) -> dict:
    issues = []
    # Placeholder cross-service validation checks (billing/service/ticket state).
    issues = [c for c in issues if c]
    return {"issues": issues, "billing_account": "UNKNOWN", "open_invoices": "UNKNOWN"}


def approve_transfer(session: Session, tenant_id, transfer_id: uuid.UUID, *, approved_by: str,
                     correlation_id: str | None = None) -> CustomerTransfer:
    request_id = correlation(correlation_id)
    transfer = session.get(CustomerTransfer, transfer_id)
    if transfer is None or transfer.tenant_id != tenant_id:
        raise NotFoundError("transfer not found")
    try:
        transfer_transition(transfer.state, "VALIDATING")
        transfer_transition("VALIDATING", "APPROVED")
    except ValueError as error:
        raise ValidationError(str(error)) from error
    transfer.state = "APPROVED"
    transfer.approved_by = approved_by
    session.flush()
    ownership = session.scalars(select(CustomerOwnership).where(
        CustomerOwnership.tenant_id == tenant_id,
        CustomerOwnership.customer_id == transfer.customer_id)).first()
    if ownership is not None:
        before = {"owning": str(ownership.owning_org_unit_id)}
        ownership.owning_org_unit_id = transfer.to_owner_id
        session.add(OwnershipHistory(tenant_id=tenant_id, customer_id=transfer.customer_id,
                                     change_type="TRANSFERRED", before=before,
                                     after={"owning": str(transfer.to_owner_id)},
                                     changed_by=approved_by, correlation_id=request_id))
    transfer.state = "COMPLETED"
    session.flush()
    audit(session, tenant_id, approved_by, "ownership.transfer_completed", resource_type="customer",
          resource_id=transfer.customer_id, correlation_id=request_id)
    outbox(session, "tenancy.customer.transferred.v1", tenant_id, request_id,
           {"tenant_id": str(tenant_id), "customer_id": transfer.customer_id,
            "to_owner": str(transfer.to_owner_id)})
    return transfer


def create_grant(session: Session, tenant_id, *, granting_org_unit_id: uuid.UUID,
                 receiving_org_unit_id: uuid.UUID, resource_type: str, resource_scope: dict,
                 permission: str, purpose: str | None, ends_at=None, approved_by: str,
                 actor: str = "system") -> DataAccessGrant:
    get_org_unit_or_404(session, tenant_id, granting_org_unit_id)
    get_org_unit_or_404(session, tenant_id, receiving_org_unit_id)
    grant = DataAccessGrant(tenant_id=tenant_id, granting_org_unit_id=granting_org_unit_id,
                            receiving_org_unit_id=receiving_org_unit_id, resource_type=resource_type,
                            resource_scope=resource_scope, permission=permission, purpose=purpose,
                            ends_at=ends_at, approved_by=approved_by)
    session.add(grant)
    session.flush()
    audit(session, tenant_id, actor, "data_access_grant.created", resource_type="data_access_grant",
          resource_id=grant.id, after={"granting": str(granting_org_unit_id),
                                       "receiving": str(receiving_org_unit_id), "permission": permission})
    return grant
