"""Customer merge with preview, audit and downstream events. Merging preserves
external references, interactions, KYC history and audit history. Merged IDs
redirect safely via alias records."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, func, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from ..database import Base
from ..models import (Address, Contact, Customer, CustomerOwnership, ExternalReference, KycCase, ServiceLocation, TimelineEntry)
from .audit_service import audit, correlation, outbox, timeline


class CustomerAlias(Base):
    """Redirect table: a merged customer id points at the surviving customer."""
    __tablename__ = "crm_customer_aliases"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crm_tenants.id"), index=True, nullable=False)
    alias_customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crm_customers.id"), index=True, nullable=False)
    surviving_customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("crm_customers.id"), index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def _customer(session: Session, tenant_id, customer_id) -> Customer:
    customer = session.scalar(select(Customer).where(Customer.id == customer_id, Customer.tenant_id == tenant_id))
    if customer is None:
        raise ValueError("customer not found")
    return customer


def _count(session: Session, model, tenant_id, customer_id) -> int:
    return len(list(session.scalars(select(model).where(model.tenant_id == tenant_id, model.customer_id == customer_id))))


def merge_preview(session: Session, tenant_id, primary_id, duplicate_id) -> dict:
    """Preview what a merge would do (no mutation)."""
    primary = _customer(session, tenant_id, primary_id)
    duplicate = _customer(session, tenant_id, duplicate_id)
    if primary.id == duplicate.id:
        raise ValueError("cannot merge a customer with itself")
    return {
        "primary": {"customer_id": str(primary.id), "customer_number": primary.customer_number},
        "duplicate": {"customer_id": str(duplicate.id), "customer_number": duplicate.customer_number},
        "contacts_to_move": _count(session, Contact, tenant_id, duplicate.id),
        "addresses_to_move": _count(session, Address, tenant_id, duplicate.id),
        "service_locations_to_move": _count(session, ServiceLocation, tenant_id, duplicate.id),
        "kyc_cases_to_move": _count(session, KycCase, tenant_id, duplicate.id),
        "external_references_to_move": _count(session, ExternalReference, tenant_id, duplicate.id),
        "timeline_entries_to_keep": _count(session, TimelineEntry, tenant_id, duplicate.id),
        "requires_review": True,
    }


def execute_merge(session: Session, tenant_id, primary_id, duplicate_id, actor: str | None = None) -> Customer:
    """Merge `duplicate` into `primary`. Preserves all child records by moving
    ownership to the primary customer; the duplicate remains as an alias."""
    primary = _customer(session, tenant_id, primary_id)
    duplicate = _customer(session, tenant_id, duplicate_id)
    if primary.id == duplicate.id:
        raise ValueError("cannot merge a customer with itself")

    for model in (Contact, Address, ServiceLocation, KycCase, ExternalReference, CustomerOwnership, TimelineEntry):
        for item in session.scalars(select(model).where(model.tenant_id == tenant_id, getattr(model, "customer_id") == duplicate.id)):
            item.customer_id = primary.id
            session.add(item)

    session.add(CustomerAlias(tenant_id=tenant_id, alias_customer_id=duplicate.id, surviving_customer_id=primary.id))
    duplicate.lifecycle_state = "CLOSED"
    duplicate.status = "merged"

    request_id = correlation(None)
    audit(session, tenant_id, actor or "system", "crm.customer.merged", "customer", primary.id, safe_after={"duplicate_id": str(duplicate.id)}, correlation_id=request_id)
    outbox(session, "crm.customer.merged.v1", tenant_id, request_id, {"primary_customer_id": str(primary.id), "duplicate_customer_id": str(duplicate.id)})
    timeline(session, tenant_id, "CUSTOMER", f"Merged duplicate {duplicate.customer_number} into {primary.customer_number}", actor=actor, customer_id=primary.id, correlation_id=request_id)
    session.flush()
    return primary


def resolve_customer_id(session: Session, tenant_id, customer_id):
    """Resolve a possibly-merged customer id to the surviving customer."""
    alias = session.scalar(select(CustomerAlias).where(CustomerAlias.tenant_id == tenant_id, CustomerAlias.alias_customer_id == customer_id))
    if alias is not None:
        return alias.surviving_customer_id
    return customer_id

