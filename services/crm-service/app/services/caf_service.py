"""Customer Application Form (CAF) records. CAF state is separate from KYC and
network activation state."""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import CafRecord
from ..state_machine import caf_transition
from .audit_service import audit, correlation, outbox, timeline


def get_caf(session: Session, tenant_id, caf_id) -> CafRecord:
    caf = session.scalar(select(CafRecord).where(CafRecord.id == caf_id, CafRecord.tenant_id == tenant_id))
    if caf is None:
        raise ValueError("CAF record not found")
    return caf


def create_caf(session: Session, tenant_id, payload: dict, actor: str | None = None) -> CafRecord:
    caf = CafRecord(
        tenant_id=tenant_id, caf_number="", customer_id=payload.get("customer_id"), lead_id=payload.get("lead_id"),
        lead_source=(payload.get("lead_source") or "").upper() or None,
        application_date=payload.get("application_date") or date.today(),
        franchise_id=payload.get("franchise_id"), branch_id=payload.get("branch_id"),
        requested_services=payload.get("requested_services") or [],
        declaration_accepted=bool(payload.get("declaration_accepted")),
        document_checklist=payload.get("document_checklist") or {},
        status="DRAFT", version=1, created_by=actor,
    )
    session.add(caf)
    session.flush()
    caf.caf_number = f"CAF-{caf.id.hex[:10].upper()}"
    request_id = correlation(None)
    audit(session, tenant_id, actor or "system", "crm.caf.created", "caf", caf.id, safe_after={"caf_number": caf.caf_number, "status": "DRAFT"}, correlation_id=request_id)
    session.flush()
    return caf


def submit_caf(session: Session, tenant_id, caf_id, actor: str | None = None) -> CafRecord:
    caf = get_caf(session, tenant_id, caf_id)
    caf.status = caf_transition(caf.status, "SUBMITTED")
    request_id = correlation(None)
    audit(session, tenant_id, actor or "system", "crm.caf.submitted", "caf", caf.id, safe_after={"status": "SUBMITTED"}, correlation_id=request_id)
    timeline(session, tenant_id, "CAF", f"CAF {caf.caf_number} submitted", actor=actor, customer_id=caf.customer_id, lead_id=caf.lead_id, correlation_id=request_id)
    session.flush()
    return caf


def approve_caf(session: Session, tenant_id, caf_id, reviewer: str | None = None) -> CafRecord:
    caf = get_caf(session, tenant_id, caf_id)
    caf.status = caf_transition(caf.status, "APPROVED")
    caf.reviewer_id = reviewer
    request_id = correlation(None)
    audit(session, tenant_id, reviewer or "system", "crm.caf.approved", "caf", caf.id, safe_after={"status": "APPROVED"}, correlation_id=request_id)
    outbox(session, "crm.caf.approved.v1", tenant_id, request_id, {"caf_id": str(caf.id), "customer_id": str(caf.customer_id) if caf.customer_id else None, "caf_number": caf.caf_number})
    timeline(session, tenant_id, "CAF", f"CAF {caf.caf_number} approved", actor=reviewer, customer_id=caf.customer_id, lead_id=caf.lead_id, correlation_id=request_id)
    session.flush()
    return caf


def reject_caf(session: Session, tenant_id, caf_id, reason: str, reviewer: str | None = None) -> CafRecord:
    caf = get_caf(session, tenant_id, caf_id)
    caf.status = caf_transition(caf.status, "REJECTED")
    caf.rejection_reason = reason
    caf.reviewer_id = reviewer
    request_id = correlation(None)
    audit(session, tenant_id, reviewer or "system", "crm.caf.rejected", "caf", caf.id, safe_after={"status": "REJECTED"}, reason=reason, correlation_id=request_id)
    timeline(session, tenant_id, "CAF", f"CAF {caf.caf_number} rejected", actor=reviewer, customer_id=caf.customer_id, lead_id=caf.lead_id, correlation_id=request_id)
    session.flush()
    return caf
