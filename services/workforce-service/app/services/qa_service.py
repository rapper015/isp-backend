"""Quality verification service.

Completion policies: technician self-verification, supervisor review, mandatory
QA per work-order type, mandatory QA for new technicians, QA after failed
visits. A rejected QA submission creates a rework event and returns the work
order to a controlled state."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.exceptions import NotFoundError, QAError
from ..enums import QA_STATES
from ..models import ProofOfWork, QualityReview, WorkOrder, WorkOrderResult
from .audit_service import append_event, outbox
from .flow import transition_work_order


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_review(session: Session, work_order: WorkOrder) -> QualityReview | None:
    return session.scalars(select(QualityReview).where(QualityReview.work_order_id == work_order.id)).first()


def qa_required(work_order: WorkOrder) -> bool:
    completion = work_order.completion_requirements or {}
    return bool(completion.get("require_qa"))


def open_review(session: Session, tenant_id, work_order: WorkOrder) -> QualityReview:
    review = get_review(session, work_order)
    if not qa_required(work_order):
        if review is None:
            review = QualityReview(tenant_id=tenant_id, work_order_id=work_order.id, state="NOT_REQUIRED")
            session.add(review)
        else:
            review.state = "NOT_REQUIRED"
        session.flush()
        return review
    if review is None:
        review = QualityReview(tenant_id=tenant_id, work_order_id=work_order.id, state="PENDING")
        session.add(review)
    elif review.state in ("APPROVED", "UNDER_REVIEW"):
        pass
    else:
        review.state = "PENDING"
    session.flush()
    return review


def run_review_checks(session: Session, tenant_id, work_order: WorkOrder) -> dict:
    """Deterministic QA validation checks."""
    checks = {}
    from . import checklist_service, proof_service

    checklist_ok, checklist_errors = checklist_service.checklist_is_complete(session, tenant_id, work_order)
    checks["checklist_complete"] = checklist_ok
    checks["checklist_errors"] = checklist_errors
    checks["required_proof_missing"] = proof_service.required_proof_missing(session, tenant_id, work_order)
    checks["material_reconciliation_errors"] = proof_service.material_reconciliation_errors(session, tenant_id, work_order)

    from ..models import CustomerAcknowledgement, VisitCheckIn

    checks["checkin_present"] = session.scalars(
        select(VisitCheckIn.id).where(VisitCheckIn.work_order_id == work_order.id).limit(1)).first() is not None
    completion = work_order.completion_requirements or {}
    checks["customer_ack_present"] = session.scalars(
        select(CustomerAcknowledgement.id).where(CustomerAcknowledgement.work_order_id == work_order.id).limit(1)).first() is not None \
        or not completion.get("require_acknowledgement")
    return checks


def approve_review(session: Session, tenant_id, work_order_id: uuid.UUID, *, reviewer: str,
                   reason: str | None = None, correlation_id: str | None = None) -> QualityReview:
    work_order = session.get(WorkOrder, work_order_id)
    if work_order is None or work_order.tenant_id != tenant_id:
        raise NotFoundError("work order not found")
    review = get_review(session, work_order) or open_review(session, tenant_id, work_order)
    if review.state == "NOT_REQUIRED":
        return review
    if review.state not in ("PENDING", "UNDER_REVIEW"):
        raise QAError(f"review cannot be approved from state {review.state}")
    checks = run_review_checks(session, tenant_id, work_order)
    review.state = "UNDER_REVIEW"
    review.review_checks = checks
    review.reviewer = reviewer
    review.reviewed_at = _now()
    session.flush()

    # Approval requires passing checks.
    failed = checks.get("required_proof_missing") or checks.get("material_reconciliation_errors") \
        or not checks.get("checklist_complete")
    if failed:
        review.state = "REJECTED"
        review.decision = "REJECTED"
        review.reason = reason or "QA checks failed"
        session.flush()
        append_event(session, work_order, "work_order.qa_rejected",
                     payload={"review_id": str(review.id), "reason": review.reason},
                     actor_type="agent", actor_id=reviewer, correlation_id=correlation_id or work_order.correlation_id)
        outbox(session, "workforce.work_order.qa_rejected.v1", tenant_id,
               correlation_id or work_order.correlation_id,
               {"work_order_id": str(work_order.id), "work_order_number": work_order.work_order_number})
        raise QAError("QA checks failed: " + str(checks))

    review.state = "APPROVED"
    review.decision = "APPROVED"
    review.reason = reason or "approved"
    review.reviewed_at = _now()
    session.flush()

    # Record the result if not already present and complete the work order.
    if session.scalars(select(WorkOrderResult.id).where(WorkOrderResult.work_order_id == work_order.id)).first() is None:
        session.add(WorkOrderResult(tenant_id=tenant_id, work_order_id=work_order.id,
                                    result_code=work_order.result_code or "OTHER",
                                    summary=work_order.result_summary or "Completed (QA approved)",
                                    recorded_by=reviewer))
    if work_order.result_code is None:
        work_order.result_code = "OTHER"
        work_order.result_summary = "Completed (QA approved)"
    work_order = transition_work_order(session, tenant_id, work_order, "COMPLETED", event_type="work_order.completed",
                                       payload={"qa": "approved"}, actor=reviewer,
                                       correlation_id=correlation_id or work_order.correlation_id)
    from . import workorder_service

    workorder_service._mark_field_sla_completed(session, work_order)
    if work_order.assigned_technician_id:
        from . import technician_service

        technician_service.transition_status(session, tenant_id, work_order.assigned_technician_id, to_status="AVAILABLE",
                                             work_order_id=work_order.id, source="SYSTEM", actor=reviewer,
                                             correlation_id=correlation_id)
    outbox(session, "workforce.work_order.qa_approved.v1", tenant_id, correlation_id or work_order.correlation_id,
           {"work_order_id": str(work_order.id), "work_order_number": work_order.work_order_number})
    outbox(session, "workforce.work_order.completed.v1", tenant_id, correlation_id or work_order.correlation_id,
           {"work_order_id": str(work_order.id), "work_order_number": work_order.work_order_number})
    session.flush()
    return review


def reject_review(session: Session, tenant_id, work_order_id: uuid.UUID, *, reviewer: str, reason: str,
                  rework: bool = True, correlation_id: str | None = None) -> QualityReview:
    work_order = session.get(WorkOrder, work_order_id)
    if work_order is None or work_order.tenant_id != tenant_id:
        raise NotFoundError("work order not found")
    if not reason or not reason.strip():
        raise QAError("rejection requires a reason")
    review = get_review(session, work_order)
    if review is None:
        review = open_review(session, tenant_id, work_order)
    review.state = "REWORK_REQUIRED" if rework else "REJECTED"
    review.decision = "REWORK_REQUIRED" if rework else "REJECTED"
    review.reviewer = reviewer
    review.reason = reason
    review.reviewed_at = _now()
    session.flush()
    target = "QA_REJECTED"
    work_order = transition_work_order(session, tenant_id, work_order, target, event_type="work_order.qa_rejected",
                                       payload={"review_id": str(review.id), "reason": reason}, actor=reviewer,
                                       correlation_id=correlation_id or work_order.correlation_id)
    outbox(session, "workforce.work_order.qa_rejected.v1", tenant_id, correlation_id or work_order.correlation_id,
           {"work_order_id": str(work_order.id), "work_order_number": work_order.work_order_number, "reason": reason})
    session.flush()
    return review


def pending_reviews(session: Session, tenant_id, *, limit: int = 200) -> list[QualityReview]:
    return list(session.scalars(
        select(QualityReview).where(QualityReview.tenant_id == tenant_id,
                                    QualityReview.state.in_(("PENDING", "UNDER_REVIEW")))
        .order_by(QualityReview.created_at).limit(limit)))
