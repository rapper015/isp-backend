"""Tenant-scoped duplicate detection. Never auto-merges on fuzzy name alone."""
from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..models import Customer, Lead
from ..validation import normalize_email, normalize_phone


def find_duplicate_customers(session: Session, tenant_id, phone: str | None = None, email: str | None = None, caf_number: str | None = None, gstin: str | None = None) -> list[dict]:
    """Return potential duplicate customers with a match score (0..1)."""
    phone = normalize_phone(phone)
    email = normalize_email(email)
    conditions = []
    if phone:
        conditions.append(Customer.phone == phone)
    if email:
        conditions.append(Customer.email == email)
    if caf_number:
        conditions.append(Customer.caf_number == caf_number)
    if gstin:
        conditions.append(Customer.gstin == gstin)
    if not conditions:
        return []
    candidates = list(session.scalars(select(Customer).where(Customer.tenant_id == tenant_id, or_(*conditions))))
    matches = []
    for customer in candidates:
        score = 0.0
        signals = []
        if phone and customer.phone == phone:
            score += 0.6
            signals.append("mobile")
        if email and customer.email == email:
            score += 0.3
            signals.append("email")
        if caf_number and customer.caf_number == caf_number:
            score += 0.7
            signals.append("caf_number")
        if gstin and customer.gstin == gstin:
            score += 0.7
            signals.append("gstin")
        matches.append({"customer_id": str(customer.id), "customer_number": customer.customer_number, "score": round(min(score, 1.0), 2), "signals": signals})
    return sorted(matches, key=lambda item: item["score"], reverse=True)


def find_duplicate_leads(session: Session, tenant_id, mobile: str, email: str | None = None) -> list[dict]:
    phone = normalize_phone(mobile)
    email = normalize_email(email)
    statement = select(Lead).where(Lead.tenant_id == tenant_id, Lead.primary_mobile == phone)
    if email:
        statement = statement.where(or_(Lead.primary_mobile == phone, Lead.primary_email == email))
    leads = list(session.scalars(statement))
    return [{"lead_id": str(item.id), "lead_number": item.lead_number, "stage": item.stage} for item in leads]
