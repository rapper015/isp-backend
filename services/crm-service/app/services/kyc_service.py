"""KYC cases and documents. Documents are stored as private storage references;
only masked identifiers are kept. No Aadhaar/PAN verification is faked — manual
verification is explicit when no provider is configured."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..enums import KYC_DOCUMENT_TYPES, KYC_TYPES
from ..models import KycCase, KycDocument
from ..state_machine import kyc_transition
from ..validation import mask_identifier
from .audit_service import audit, correlation, outbox, timeline


def get_kyc_case(session: Session, tenant_id, case_id):
    case = session.scalar(select(KycCase).where(KycCase.id == case_id, KycCase.tenant_id == tenant_id))
    if case is None:
        raise ValueError("KYC case not found")
    return case


def create_kyc_case(session: Session, tenant_id, customer_id, lead_id=None, kyc_type: str = "INDIVIDUAL", actor: str | None = None):
    if kyc_type not in KYC_TYPES:
        raise ValueError(f"invalid KYC type: {kyc_type}")
    case = KycCase(tenant_id=tenant_id, customer_id=customer_id, lead_id=lead_id, kyc_type=kyc_type, status="DRAFT", created_by=actor)
    session.add(case)
    request_id = correlation(None)
    audit(session, tenant_id, actor or "system", "crm.kyc.created", "kyc_case", case.id, safe_after={"kyc_type": kyc_type}, correlation_id=request_id)
    session.flush()
    return case


def submit_kyc(session: Session, tenant_id, case_id, actor: str | None = None):
    case = get_kyc_case(session, tenant_id, case_id)
    case.status = kyc_transition(case.status, "SUBMITTED")
    case.submitted_at = datetime.now(timezone.utc)
    request_id = correlation(None)
    audit(session, tenant_id, actor or "system", "crm.kyc.submitted", "kyc_case", case.id, safe_after={"status": "SUBMITTED"}, correlation_id=request_id)
    outbox(session, "crm.kyc.submitted.v1", tenant_id, request_id, {"kyc_case_id": str(case.id), "customer_id": str(case.customer_id) if case.customer_id else None})
    timeline(session, tenant_id, "KYC", "KYC submitted", actor=actor, customer_id=case.customer_id, lead_id=case.lead_id, correlation_id=request_id)
    session.flush()
    return case


def request_more_information(session: Session, tenant_id, case_id, actor: str | None = None):
    case = get_kyc_case(session, tenant_id, case_id)
    case.status = kyc_transition(case.status, "ADDITIONAL_INFO_REQUIRED")
    request_id = correlation(None)
    audit(session, tenant_id, actor or "system", "crm.kyc.requested_information", "kyc_case", case.id, safe_after={"status": "ADDITIONAL_INFO_REQUIRED"}, correlation_id=request_id)
    session.flush()
    return case


def verify_kyc(session: Session, tenant_id, case_id, method: str = "manual", reviewer: str | None = None):
    case = get_kyc_case(session, tenant_id, case_id)
    case.status = kyc_transition(case.status, "VERIFIED")
    case.verification_method = method
    case.reviewer_id = reviewer
    case.verified_at = datetime.now(timezone.utc)
    request_id = correlation(None)
    audit(session, tenant_id, reviewer or "system", "crm.kyc.verified", "kyc_case", case.id, safe_after={"status": "VERIFIED", "method": method}, correlation_id=request_id)
    outbox(session, "crm.kyc.verified.v1", tenant_id, request_id, {"kyc_case_id": str(case.id), "customer_id": str(case.customer_id) if case.customer_id else None, "method": method})
    timeline(session, tenant_id, "KYC", "KYC verified", actor=reviewer, customer_id=case.customer_id, lead_id=case.lead_id, correlation_id=request_id)
    session.flush()
    return case


def reject_kyc(session: Session, tenant_id, case_id, reason: str, reviewer: str | None = None):
    case = get_kyc_case(session, tenant_id, case_id)
    case.status = kyc_transition(case.status, "REJECTED")
    case.rejection_reason = reason
    case.reviewer_id = reviewer
    request_id = correlation(None)
    audit(session, tenant_id, reviewer or "system", "crm.kyc.rejected", "kyc_case", case.id, safe_after={"status": "REJECTED"}, reason=reason, correlation_id=request_id)
    outbox(session, "crm.kyc.rejected.v1", tenant_id, request_id, {"kyc_case_id": str(case.id), "customer_id": str(case.customer_id) if case.customer_id else None})
    timeline(session, tenant_id, "KYC", "KYC rejected", actor=reviewer, customer_id=case.customer_id, lead_id=case.lead_id, correlation_id=request_id)
    session.flush()
    return case


def add_kyc_document(session: Session, tenant_id, case_id, document_type: str, storage_reference: str, masked_identifier: str | None = None, content_type: str | None = None, size_bytes: int = 0, checksum: str | None = None):
    if document_type not in KYC_DOCUMENT_TYPES:
        raise ValueError(f"invalid document type: {document_type}")
    case = get_kyc_case(session, tenant_id, case_id)
    document = KycDocument(
        tenant_id=tenant_id, kyc_case_id=case.id, customer_id=case.customer_id, document_type=document_type,
        masked_identifier=mask_identifier(masked_identifier), storage_reference=storage_reference,
        content_type=content_type, size_bytes=int(size_bytes), checksum=checksum, verification_state="PENDING",
    )
    session.add(document)
    request_id = correlation(None)
    audit(session, tenant_id, "system", "crm.kyc.document_added", "kyc_document", document.id, safe_after={"document_type": document_type, "masked_identifier": document.masked_identifier}, correlation_id=request_id)
    session.flush()
    return document


def list_kyc_documents(session: Session, tenant_id, case_id) -> list:
    get_kyc_case(session, tenant_id, case_id)
    documents = list(session.scalars(select(KycDocument).where(KycDocument.tenant_id == tenant_id, KycDocument.kyc_case_id == case_id)))
    # Never return storage references without the sensitive-document permission;
    # the API layer enforces that and this service returns safe metadata only.
    return documents
