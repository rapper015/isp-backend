"""Customer 360 aggregation. CRM-owned data is read from the local database;
downstream data (BSS/OSS/AAA/NMS/Workforce) comes from stored external
references/read projections. Downstream sections are clearly marked stale or
unavailable when not present; no slow synchronous fan-out is performed."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..cache import get_json, key, set_json
from ..models import (Address, Contact, Customer, CustomerLifecycleEvent, CustomerOwnership, ExternalReference, KycCase, Lead, LeadInteraction, ServiceLocation, TimelineEntry)
from .risk_service import risk_history


def customer_360(session: Session, tenant_id, customer_id, use_cache: bool = True) -> dict:
    cache_key = key(str(tenant_id), "customer360", str(customer_id))
    if use_cache:
        cached = get_json(cache_key)
        if cached:
            return cached
    customer = session.scalar(select(Customer).where(Customer.id == customer_id, Customer.tenant_id == tenant_id))
    if customer is None:
        raise ValueError("customer not found")

    contacts = [{"id": str(item.id), "role": item.role, "contact_person_name": item.contact_person_name, "mobile": item.mobile, "email": item.email, "is_primary": item.is_primary, "verification_state": item.verification_state} for item in session.scalars(select(Contact).where(Contact.tenant_id == tenant_id, Contact.customer_id == customer.id))]
    addresses = [{"id": str(item.id), "address_type": item.address_type, "city": item.city, "state": item.state, "zipcode": item.zipcode, "formatted_address": item.formatted_address, "version": item.version} for item in session.scalars(select(Address).where(Address.tenant_id == tenant_id, Address.customer_id == customer.id))]
    service_locations = [{"id": str(item.id), "service_location_number": item.service_location_number, "alias": item.alias, "status": item.status} for item in session.scalars(select(ServiceLocation).where(ServiceLocation.tenant_id == tenant_id, ServiceLocation.customer_id == customer.id))]
    kyc_cases = [{"id": str(item.id), "kyc_type": item.kyc_type, "status": item.status, "verification_method": item.verification_method} for item in session.scalars(select(KycCase).where(KycCase.tenant_id == tenant_id, KycCase.customer_id == customer.id))]
    ownership = [{"owner_type": item.owner_type, "owner_id": item.owner_id, "role": item.role, "is_primary": item.is_primary} for item in session.scalars(select(CustomerOwnership).where(CustomerOwnership.tenant_id == tenant_id, CustomerOwnership.customer_id == customer.id))]
    lifecycle_events = [{"from_state": item.from_state, "to_state": item.to_state, "trigger": item.trigger, "reason": item.reason, "created_at": item.created_at.isoformat() if item.created_at else None} for item in session.scalars(select(CustomerLifecycleEvent).where(CustomerLifecycleEvent.tenant_id == tenant_id, CustomerLifecycleEvent.customer_id == customer.id).order_by(CustomerLifecycleEvent.created_at.desc()).limit(20))]
    leads = [{"id": str(item.id), "lead_number": item.lead_number, "stage": item.stage, "lead_source": item.lead_source} for item in session.scalars(select(Lead).where(Lead.tenant_id == tenant_id, Lead.converted_customer_id == customer.id))]
    interactions = [{"id": str(item.id), "channel": item.channel, "direction": item.direction, "subject": item.subject, "safe_summary": item.safe_summary} for item in session.scalars(select(LeadInteraction).where(LeadInteraction.tenant_id == tenant_id, LeadInteraction.customer_id == customer.id).limit(50))]
    risk = [{"level": item.level, "source": item.source, "reason": item.reason, "effective_level": item.effective_level, "created_at": item.created_at.isoformat() if item.created_at else None} for item in risk_history(session, tenant_id, customer.id)[:10]]
    timeline = [{"category": item.category, "safe_summary": item.safe_summary, "actor": item.actor, "occurred_at": item.occurred_at.isoformat() if item.occurred_at else None} for item in session.scalars(select(TimelineEntry).where(TimelineEntry.tenant_id == tenant_id, TimelineEntry.customer_id == customer.id).order_by(TimelineEntry.occurred_at.desc()).limit(100))]

    # Downstream references / read projections (marked stale/unavailable).
    external = {}
    for reference in session.scalars(select(ExternalReference).where(ExternalReference.tenant_id == tenant_id, ExternalReference.customer_id == customer.id)):
        external.setdefault(reference.service_name, []).append({
            "external_type": reference.external_type, "external_id": reference.external_id,
            "external_status": reference.external_status, "last_synced_at": reference.last_synced_at,
            "projection": reference.safe_projection,
        })

    result = {
        "customer": {
            "id": str(customer.id), "customer_number": customer.customer_number, "customer_code": customer.customer_code,
            "full_name": customer.full_name, "customer_type": customer.customer_type, "phone": customer.phone, "email": customer.email,
            "gstin": customer.gstin, "acquisition_source": customer.acquisition_source,
            "lifecycle_state": customer.lifecycle_state, "risk_level": customer.risk_level, "status": customer.status,
        },
        "contacts": contacts,
        "addresses": addresses,
        "service_locations": service_locations,
        "kyc_cases": kyc_cases,
        "ownership": ownership,
        "lifecycle": lifecycle_events,
        "leads": leads,
        "interactions": interactions,
        "risk": risk,
        "timeline": timeline,
        "external": {"available": bool(external), "sections": external, "stale_or_unavailable": not bool(external)},
    }
    set_json(cache_key, result, ttl=30)
    return result


def invalidate_customer_360(tenant_id, customer_id) -> None:
    from ..cache import delete_key
    delete_key(key(str(tenant_id), "customer360", str(customer_id)))
