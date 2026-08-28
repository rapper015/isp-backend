"""Lead-to-customer conversion: transactional and idempotent. A lead may be
converted at most once; retries return the existing customer."""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Address, Contact, Customer, CustomerOwnership, ExternalReference, KycCase, Lead, ServiceLocation
from ..validation import normalize_email, normalize_phone
from .audit_service import audit, correlation, outbox, timeline


def _customer_number(customer: Customer) -> str:
    return f"CUS-{customer.id.hex[:10].upper()}"


def _service_location_number(customer: Customer) -> str:
    return f"LOC-{customer.id.hex[:10].upper()}"


def convert_lead(session: Session, tenant_id, lead_id, payload: dict | None = None, actor: str | None = None, request_bss: bool = True, request_oss: bool = True) -> Customer:
    """Convert a WON lead into a customer. Idempotent: a lead already converted
    returns its existing customer."""
    payload = payload or {}
    lead = session.scalar(select(Lead).where(Lead.id == lead_id, Lead.tenant_id == tenant_id))
    if lead is None:
        raise ValueError("lead not found")
    if lead.converted_customer_id is not None:
        customer = session.get(Customer, lead.converted_customer_id)
        if customer is not None:
            return customer  # idempotent retry

    # 1. Revalidate tenant ownership (done via scoped query above).
    # 2. Winning lead stage must be WON (or KYC_PENDING when conversion requested).
    if lead.stage not in {"WON", "KYC_PENDING", "CONVERTED"}:
        raise ValueError(f"lead must be WON before conversion (current: {lead.stage})")

    # 3. Duplicate check by primary mobile.
    duplicate = session.scalar(select(Customer).where(Customer.tenant_id == tenant_id, Customer.phone == lead.primary_mobile))
    if duplicate is not None and str(duplicate.id) != str(lead.converted_customer_id or ""):
        raise ValueError("a customer with the same mobile already exists")

    # 4. Create the customer (preserving Milestone 0 identity fields).
    full_name = " ".join(filter(None, [lead.first_name, lead.last_name])) or lead.company_name or lead.primary_mobile
    customer = Customer(
        tenant_id=tenant_id,
        customer_number="",
        customer_code=payload.get("customer_code") or "",
        caf_number=payload.get("caf_number"),
        customer_type="BUSINESS" if lead.lead_type == "BUSINESS" else "INDIVIDUAL",
        legal_name=lead.company_name or full_name,
        full_name=full_name,
        first_name=lead.first_name,
        last_name=lead.last_name,
        company_trading_name=lead.company_name,
        phone=lead.primary_mobile,
        email=lead.primary_email,
        preferred_channel=lead.preferred_channel,
        acquisition_source=lead.lead_source,
        franchise_id=lead.franchise_id,
        branch_id=lead.branch_id,
        area=lead.area,
        lifecycle_state="ONBOARDING",
        risk_level="UNKNOWN",
        status="onboarding",
        created_by=actor,
    )
    session.add(customer)
    session.flush()
    customer.customer_number = _customer_number(customer)
    customer.customer_code = customer.customer_code or customer.customer_number

    # 5. Verified primary contact from lead mobile/email.
    session.add(Contact(tenant_id=tenant_id, customer_id=customer.id, role="CONTACT_PERSON", contact_person_name=full_name, mobile=customer.phone, email=customer.email, verification_state="UNVERIFIED", is_primary=True, source="lead_conversion"))

    # 6. Convert the installation-address draft into a structured address + service location.
    draft = lead.installation_address_draft or {}
    if draft:
        address = Address(
            tenant_id=tenant_id, customer_id=customer.id, address_type="INSTALLATION",
            country=draft.get("country"), state=draft.get("state"), city=draft.get("city"),
            zipcode=draft.get("zipcode"), door_number=draft.get("door_number"), street=draft.get("street"),
            area=draft.get("area"), colony=draft.get("colony"), building=draft.get("building"),
            landmark=draft.get("landmark"), house_type=draft.get("house_type"),
            formatted_address=draft.get("formatted_address"),
            latitude=draft.get("latitude"), longitude=draft.get("longitude"),
            verification_state="UNVERIFIED", version=1,
        )
        session.add(address)
        session.flush()
        session.add(ServiceLocation(tenant_id=tenant_id, customer_id=customer.id, address_id=address.id, service_location_number=_service_location_number(customer), alias=full_name, status="PLANNED"))

    # 7. Link KYC case if present (move case to customer).
    kyc_case = session.scalar(select(KycCase).where(KycCase.lead_id == lead.id, KycCase.tenant_id == tenant_id).order_by(KycCase.created_at.desc()).limit(1))
    if kyc_case is not None:
        kyc_case.customer_id = customer.id

    # 8. Ownership relationship.
    if lead.franchise_id:
        session.add(CustomerOwnership(tenant_id=tenant_id, customer_id=customer.id, owner_type="FRANCHISE", owner_id=str(lead.franchise_id), role="OWNER", is_primary=True))
    if lead.branch_id:
        session.add(CustomerOwnership(tenant_id=tenant_id, customer_id=customer.id, owner_type="BRANCH", owner_id=str(lead.branch_id), role="OWNER", is_primary=True))

    # 9. Mark lead converted and link the customer.
    from ..state_machine import lead_transition
    if lead.stage != "CONVERTED":
        lead.stage = lead_transition(lead.stage, "CONVERTED")
    lead.converted_customer_id = customer.id

    # 9b. Backfill the customer id onto the lead's timeline and interactions so
    # the unified customer timeline includes the pre-conversion history.
    from ..models import LeadInteraction, TimelineEntry
    for entry in session.scalars(select(TimelineEntry).where(TimelineEntry.tenant_id == tenant_id, TimelineEntry.lead_id == lead.id, TimelineEntry.customer_id.is_(None))):
        entry.customer_id = customer.id
    for interaction in session.scalars(select(LeadInteraction).where(LeadInteraction.tenant_id == tenant_id, LeadInteraction.lead_id == lead.id, LeadInteraction.customer_id.is_(None))):
        interaction.customer_id = customer.id

    request_id = correlation(None)
    # 10/11. Publish events.
    outbox(session, "crm.customer.created.v1", tenant_id, request_id, {"customer_id": str(customer.id), "customer_number": customer.customer_number, "lead_id": str(lead.id), "acquisition_source": lead.lead_source})
    outbox(session, "crm.lead.converted.v1", tenant_id, request_id, {"lead_id": str(lead.id), "customer_id": str(customer.id)})
    # 12/13. Optional downstream requests.
    if request_bss:
        outbox(session, "crm.billing_account.requested.v1", tenant_id, request_id, {"customer_id": str(customer.id), "customer_number": customer.customer_number})
    if request_oss:
        outbox(session, "crm.service_order.requested.v1", tenant_id, request_id, {"customer_id": str(customer.id), "service_location_number": _service_location_number(customer), "requested_service": lead.requested_service, "plan_reference": lead.requested_plan_reference})
    audit(session, tenant_id, actor or "system", "crm.lead.converted", "lead", lead.id, safe_after={"customer_id": str(customer.id)}, correlation_id=request_id)
    timeline(session, tenant_id, "CUSTOMER", f"Customer {customer.customer_number} created from lead", actor=actor, customer_id=customer.id, lead_id=lead.id, correlation_id=request_id)
    session.flush()
    return customer
