"""Customer profile, contacts, structured addresses and service locations."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..enums import ADDRESS_TYPES, CONTACT_ROLES, CONTACT_VERIFICATION
from ..models import Address, Contact, Customer, ServiceLocation
from ..validation import ValidationError, normalize_email, normalize_phone, validate_coordinates, validate_zipcode
from .audit_service import audit, correlation, outbox, timeline


def get_customer(session: Session, tenant_id, customer_id) -> Customer:
    customer = session.scalar(select(Customer).where(Customer.id == customer_id, Customer.tenant_id == tenant_id))
    if customer is None:
        raise ValueError("customer not found")
    return customer


def create_customer(session: Session, tenant_id, payload: dict, actor: str | None = None) -> Customer:
    phone = normalize_phone(payload.get("phone"))
    if not phone:
        raise ValidationError("phone is required")
    email = normalize_email(payload.get("email"))
    customer = Customer(
        tenant_id=tenant_id, customer_number="", customer_code=payload.get("customer_code") or "",
        customer_type=(payload.get("customer_type") or "INDIVIDUAL").upper(),
        full_name=(payload.get("full_name") or payload.get("legal_name") or "").strip(),
        legal_name=payload.get("legal_name"),
        first_name=payload.get("first_name"), middle_name=payload.get("middle_name"), last_name=payload.get("last_name"),
        company_trading_name=payload.get("company_trading_name"), father_or_guardian_name=payload.get("father_or_guardian_name"),
        date_of_birth=payload.get("date_of_birth"), incorporation_date=payload.get("incorporation_date"),
        gstin=payload.get("gstin"), pan_reference=payload.get("pan_reference"),
        phone=phone, email=email,
        primary_language=payload.get("primary_language"), preferred_channel=(payload.get("preferred_channel") or "").upper() or None,
        acquisition_source=payload.get("acquisition_source"),
        franchise_id=payload.get("franchise_id"), branch_id=payload.get("branch_id"), area=payload.get("area"),
        account_manager_id=payload.get("account_manager_id"),
        lifecycle_state="PROSPECT", risk_level="UNKNOWN", status="onboarding", created_by=actor,
    )
    if not customer.full_name:
        customer.full_name = customer.legal_name or customer.company_trading_name or phone
    session.add(customer)
    session.flush()
    customer.customer_number = f"CUS-{customer.id.hex[:10].upper()}"
    customer.customer_code = customer.customer_code or customer.customer_number
    request_id = correlation(None)
    audit(session, tenant_id, actor or "system", "crm.customer.created", "customer", customer.id, safe_after={"customer_number": customer.customer_number}, correlation_id=request_id)
    outbox(session, "crm.customer.created.v1", tenant_id, request_id, {"customer_id": str(customer.id), "customer_number": customer.customer_number})
    timeline(session, tenant_id, "CUSTOMER", f"Customer {customer.customer_number} created", actor=actor, customer_id=customer.id, correlation_id=request_id)
    session.flush()
    return customer


def update_customer(session: Session, tenant_id, customer_id, payload: dict, actor: str | None = None) -> Customer:
    customer = get_customer(session, tenant_id, customer_id)
    safe = {key: value for key, value in payload.items() if key not in {"phone", "email"}}
    for key, value in payload.items():
        if key == "phone":
            customer.phone = normalize_phone(value) or customer.phone
        elif key == "email":
            customer.email = normalize_email(value) or customer.email
        elif hasattr(customer, key) and value is not None:
            setattr(customer, key, value)
    request_id = correlation(None)
    audit(session, tenant_id, actor or "system", "crm.customer.updated", "customer", customer.id, safe_after=safe, correlation_id=request_id)
    outbox(session, "crm.customer.updated.v1", tenant_id, request_id, {"customer_id": str(customer.id)})
    session.flush()
    return customer


# ---------------------------------------------------------------------------
# Contacts
# ---------------------------------------------------------------------------

def add_contact(session: Session, tenant_id, customer_id, payload: dict, actor: str | None = None) -> Contact:
    get_customer(session, tenant_id, customer_id)
    role = (payload.get("role") or "CONTACT_PERSON").upper()
    if role not in CONTACT_ROLES:
        raise ValueError(f"invalid contact role: {role}")
    contact = Contact(
        tenant_id=tenant_id, customer_id=customer_id, role=role,
        contact_person_name=payload.get("contact_person_name"),
        mobile=normalize_phone(payload.get("mobile")), alternate_mobile=normalize_phone(payload.get("alternate_mobile")),
        email=normalize_email(payload.get("email")), alternate_email=normalize_email(payload.get("alternate_email")),
        landline=payload.get("landline"), whatsapp=normalize_phone(payload.get("whatsapp")),
        verification_state="UNVERIFIED", is_primary=bool(payload.get("is_primary")),
        communication_preference=payload.get("communication_preference") or {},
        consent_state=(payload.get("consent_state") or "NOT_PROVIDED").upper(),
        source=payload.get("source"),
    )
    if contact.mobile is None and contact.email is None:
        raise ValidationError("a contact needs a mobile or email")
    if contact.is_primary:
        for existing in session.scalars(select(Contact).where(Contact.tenant_id == tenant_id, Contact.customer_id == customer_id, Contact.is_primary.is_(True))):
            existing.is_primary = False
    session.add(contact)
    request_id = correlation(None)
    audit(session, tenant_id, actor or "system", "crm.customer.contact_added", "contact", contact.id, safe_after={"role": role, "is_primary": contact.is_primary}, correlation_id=request_id)
    timeline(session, tenant_id, "CUSTOMER", "Contact added", actor=actor, customer_id=customer_id, correlation_id=request_id)
    session.flush()
    return contact


def update_contact(session: Session, tenant_id, customer_id, contact_id, payload: dict, actor: str | None = None) -> Contact:
    get_customer(session, tenant_id, customer_id)
    contact = session.scalar(select(Contact).where(Contact.id == contact_id, Contact.tenant_id == tenant_id, Contact.customer_id == customer_id))
    if contact is None:
        raise ValueError("contact not found")
    for key, value in payload.items():
        if key == "mobile":
            contact.mobile = normalize_phone(value) or contact.mobile
        elif key == "email":
            contact.email = normalize_email(value) or contact.email
        elif hasattr(contact, key) and value is not None:
            setattr(contact, key, value)
    request_id = correlation(None)
    audit(session, tenant_id, actor or "system", "crm.customer.contact_updated", "contact", contact.id, safe_after={"fields": sorted(payload)}, correlation_id=request_id)
    session.flush()
    return contact


def verify_contact(session: Session, tenant_id, customer_id, contact_id, otp_verified: bool = True, actor: str | None = None) -> Contact:
    contact = update_contact(session, tenant_id, customer_id, contact_id, {"verification_state": "VERIFIED" if otp_verified else "FAILED"}, actor)
    if otp_verified:
        contact.otp_verified_at = datetime.now(timezone.utc)
        request_id = correlation(None)
        audit(session, tenant_id, actor or "system", "crm.customer.contact_verified", "contact", contact.id, safe_after={"verification_state": "VERIFIED"}, correlation_id=request_id)
        outbox(session, "crm.customer.contact_verified.v1", tenant_id, request_id, {"contact_id": str(contact.id), "customer_id": str(customer_id)})
        session.flush()
    return contact


# ---------------------------------------------------------------------------
# Addresses
# ---------------------------------------------------------------------------

def add_address(session: Session, tenant_id, customer_id, payload: dict, actor: str | None = None) -> Address:
    get_customer(session, tenant_id, customer_id)
    address_type = (payload.get("address_type") or "").upper()
    if address_type not in ADDRESS_TYPES:
        raise ValueError(f"invalid address type: {address_type}")
    validate_coordinates(payload.get("latitude"), payload.get("longitude"))
    zipcode = validate_zipcode(payload.get("zipcode"))
    address = Address(
        tenant_id=tenant_id, customer_id=customer_id, address_type=address_type,
        country=payload.get("country"), state=payload.get("state"), district=payload.get("district"),
        city=payload.get("city"), zipcode=zipcode, door_number=payload.get("door_number"),
        street=payload.get("street"), area=payload.get("area"), colony=payload.get("colony"),
        building=payload.get("building"), landmark=payload.get("landmark"), house_type=payload.get("house_type"),
        formatted_address=payload.get("formatted_address"), latitude=payload.get("latitude"), longitude=payload.get("longitude"),
        geolocation_accuracy=payload.get("geolocation_accuracy"), verification_state=(payload.get("verification_state") or "UNVERIFIED").upper(),
        valid_from=payload.get("valid_from"), valid_to=payload.get("valid_to"), version=1,
    )
    session.add(address)
    request_id = correlation(None)
    audit(session, tenant_id, actor or "system", "crm.customer.address_added", "address", address.id, safe_after={"address_type": address_type, "city": address.city}, correlation_id=request_id)
    outbox(session, "crm.customer.address_changed.v1", tenant_id, request_id, {"customer_id": str(customer_id), "address_id": str(address.id), "address_type": address_type})
    timeline(session, tenant_id, "ADDRESS", f"{address_type} address added", actor=actor, customer_id=customer_id, correlation_id=request_id)
    session.flush()
    return address


def update_address(session: Session, tenant_id, customer_id, address_id, payload: dict, actor: str | None = None) -> Address:
    """Versioned update: supersede the current address and write a new version."""
    get_customer(session, tenant_id, customer_id)
    current = session.scalar(select(Address).where(Address.id == address_id, Address.tenant_id == tenant_id, Address.customer_id == customer_id, Address.valid_to.is_(None)))
    if current is None:
        raise ValueError("address not found")
    current.valid_to = payload.get("valid_to") or datetime.now(timezone.utc).date()
    new_address = add_address(session, tenant_id, customer_id, {**{key: getattr(current, key) for key in ("country", "state", "district", "city", "zipcode", "door_number", "street", "area", "colony", "building", "landmark", "house_type", "formatted_address", "latitude", "longitude", "geolocation_accuracy") if getattr(current, key, None) is not None}, **{key: value for key, value in payload.items() if value is not None and key != "address_type"}, "address_type": current.address_type}, actor)
    new_address.version = current.version + 1
    new_address.superseded_by_id = None
    current.superseded_by_id = new_address.id
    request_id = correlation(None)
    audit(session, tenant_id, actor or "system", "crm.customer.address_updated", "address", new_address.id, safe_after={"version": new_address.version}, correlation_id=request_id)
    session.flush()
    return new_address


def address_history(session: Session, tenant_id, customer_id) -> list[Address]:
    get_customer(session, tenant_id, customer_id)
    return list(session.scalars(select(Address).where(Address.tenant_id == tenant_id, Address.customer_id == customer_id).order_by(Address.version.desc())))


def create_service_location(session: Session, tenant_id, customer_id, payload: dict, actor: str | None = None) -> ServiceLocation:
    get_customer(session, tenant_id, customer_id)
    location = ServiceLocation(
        tenant_id=tenant_id, customer_id=customer_id, address_id=payload.get("address_id"),
        service_location_number="", alias=payload.get("alias"), status=(payload.get("status") or "PLANNED").upper(),
    )
    session.add(location)
    session.flush()
    location.service_location_number = f"LOC-{location.id.hex[:10].upper()}"
    request_id = correlation(None)
    audit(session, tenant_id, actor or "system", "crm.customer.service_location_created", "service_location", location.id, safe_after={"service_location_number": location.service_location_number}, correlation_id=request_id)
    session.flush()
    return location
