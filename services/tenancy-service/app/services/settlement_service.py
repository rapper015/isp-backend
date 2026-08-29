"""Settlement workflow: cycles, partner settlements, idempotent calculation,
maker-checker approval, locking, statements, payouts, reconciliation and
disputes. Locked settlements are never edited — corrections require a reversal
or a new adjustment period."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.access import check_sod
from ..domain.exceptions import (
    FinancialError,
    NotFoundError,
    SettlementLockedError,
    ValidationError,
)
from ..domain.ledger import post_entry
from ..events import outbox
from ..models import (
    CommissionAdjustment,
    CommissionClawback,
    CommissionEarning,
    PartnerSettlement,
    PartnerStatement,
    SettlementCycle,
    SettlementDispute,
    SettlementLine,
    SettlementPayout,
    SettlementReconciliation,
    SodConstraint,
)
from ..state_machine import guarded, settlement_transition
from .audit_service import audit, correlation
from .organization_service import get_partner_or_404


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_settlement_or_404(session: Session, tenant_id, settlement_id) -> PartnerSettlement:
    settlement = session.get(PartnerSettlement, settlement_id)
    if settlement is None or settlement.tenant_id != tenant_id:
        raise NotFoundError("settlement not found")
    return settlement


def _transition(settlement: PartnerSettlement, target: str) -> None:
    try:
        settlement_transition(settlement.state, target)
    except ValueError as error:
        raise ValidationError(str(error)) from error
    settlement.state = target


def create_cycle(session: Session, tenant_id, *, code: str, period_start, period_end,
                 currency: str = "INR") -> SettlementCycle:
    existing = session.scalars(select(SettlementCycle).where(
        SettlementCycle.tenant_id == tenant_id, SettlementCycle.code == code)).first()
    if existing is not None:
        return existing
    cycle = SettlementCycle(tenant_id=tenant_id, code=code, period_start=period_start,
                            period_end=period_end, currency=currency)
    session.add(cycle)
    session.flush()
    return cycle


def create_settlement(session: Session, tenant_id, *, partner_id: uuid.UUID, cycle_id: uuid.UUID,
                      currency: str = "INR") -> PartnerSettlement:
    get_partner_or_404(session, tenant_id, partner_id)
    cycle = session.get(SettlementCycle, cycle_id)
    if cycle is None or cycle.tenant_id != tenant_id:
        raise NotFoundError("settlement cycle not found")
    existing = session.scalars(select(PartnerSettlement).where(
        PartnerSettlement.tenant_id == tenant_id, PartnerSettlement.partner_id == partner_id,
        PartnerSettlement.cycle_id == cycle.id)).first()
    if existing is not None:
        return existing
    settlement = PartnerSettlement(tenant_id=tenant_id, partner_id=partner_id, cycle_id=cycle.id,
                                   currency=currency, state="DRAFT")
    session.add(settlement)
    session.flush()
    return settlement


def calculate_settlement(session: Session, tenant_id, settlement_id: uuid.UUID, *,
                         actor: str = "system", correlation_id: str | None = None) -> PartnerSettlement:
    """Idempotent calculation: load eligible immutable earnings/adjustments/
    clawbacks and compute net. Repeated execution never duplicates lines."""
    request_id = correlation(correlation_id)
    settlement = get_settlement_or_404(session, tenant_id, settlement_id)
    if settlement.state in ("LOCKED", "PAID", "RECONCILING", "RECONCILED", "REVERSED"):
        raise ValidationError(f"cannot recalculate a {settlement.state} settlement")
    if settlement.state not in ("CALCULATING", "CALCULATED", "UNDER_REVIEW"):
        _transition(settlement, "CALCULATING")
    # Load earnings for this partner in the cycle period (not yet in settlement).
    cycle = session.get(SettlementCycle, settlement.cycle_id)
    earnings = list(session.scalars(select(CommissionEarning).where(
        CommissionEarning.tenant_id == tenant_id,
        CommissionEarning.partner_id == settlement.partner_id,
        CommissionEarning.status.in_(("RECOGNIZED", "ADJUSTED")))))
    clawbacks = list(session.scalars(select(CommissionClawback).where(
        CommissionClawback.tenant_id == tenant_id,
        CommissionClawback.earning_id.in_([e.id for e in earnings])))) if earnings else []
    adjustments = list(session.scalars(select(CommissionAdjustment).where(
        CommissionAdjustment.tenant_id == tenant_id,
        CommissionAdjustment.earning_id.in_([e.id for e in earnings])))) if earnings else []

    existing_lines = {line.source_event_id for line in session.scalars(select(SettlementLine).where(
        SettlementLine.settlement_id == settlement.id))}
    total_earnings = settlement.total_earnings
    total_clawbacks = settlement.total_clawbacks
    total_adjustments = settlement.total_adjustments
    for earning in earnings:
        if f"e:{earning.id}" in existing_lines:
            total_earnings += 0
            continue
        session.add(SettlementLine(tenant_id=tenant_id, settlement_id=settlement.id,
                                   source_event_id=f"e:{earning.id}", earning_id=earning.id,
                                   line_type="EARNING", amount=earning.amount, currency=settlement.currency))
        total_earnings += earning.amount
        earning.status = "SETTLED"
    for clawback in clawbacks:
        if f"c:{clawback.id}" in existing_lines:
            continue
        session.add(SettlementLine(tenant_id=tenant_id, settlement_id=settlement.id,
                                   source_event_id=f"c:{clawback.id}", line_type="CLAWBACK",
                                   amount=-abs(clawback.amount), currency=settlement.currency))
        total_clawbacks += abs(clawback.amount)
    for adjustment in adjustments:
        if f"a:{adjustment.id}" in existing_lines:
            continue
        session.add(SettlementLine(tenant_id=tenant_id, settlement_id=settlement.id,
                                   source_event_id=f"a:{adjustment.id}", line_type="ADJUSTMENT",
                                   amount=adjustment.amount, currency=settlement.currency))
        total_adjustments += adjustment.amount

    settlement.total_earnings = round(total_earnings, 2)
    settlement.total_clawbacks = round(total_clawbacks, 2)
    settlement.total_adjustments = round(total_adjustments, 2)
    settlement.withholding = round(total_earnings * 0.0, 2)
    settlement.net_settlement = round(
        settlement.opening_balance + total_earnings + total_adjustments - total_clawbacks -
        settlement.withholding - settlement.prior_advances, 2)
    if settlement.state != "CALCULATED":
        _transition(settlement, "CALCULATED")
    session.flush()
    audit(session, tenant_id, actor, "settlement.calculated", resource_type="settlement",
          resource_id=settlement.id, after={"net": settlement.net_settlement}, correlation_id=request_id)
    return settlement


def submit_for_review(session: Session, tenant_id, settlement_id: uuid.UUID, *, actor: str = "system") -> PartnerSettlement:
    settlement = get_settlement_or_404(session, tenant_id, settlement_id)
    _transition(settlement, "UNDER_REVIEW")
    session.flush()
    return settlement


def approve_settlement(session: Session, tenant_id, settlement_id: uuid.UUID, *, approved_by: str,
                       correlation_id: str | None = None) -> PartnerSettlement:
    request_id = correlation(correlation_id)
    settlement = get_settlement_or_404(session, tenant_id, settlement_id)
    check_sod(settlement.correlation_id or "", approved_by, operation="settlement.approve",
              constraints=list(session.scalars(select(SodConstraint).where(
                  SodConstraint.operation == "settlement.approve"))))
    _transition(settlement, "APPROVED")
    settlement.approved_by = approved_by
    settlement.approved_at = _now()
    session.flush()
    audit(session, tenant_id, approved_by, "settlement.approved", resource_type="settlement",
          resource_id=settlement.id, after={"state": "APPROVED"}, correlation_id=request_id)
    outbox(session, "tenancy.settlement.approved.v1", tenant_id, request_id,
           {"tenant_id": str(tenant_id), "settlement_id": str(settlement.id),
            "net": settlement.net_settlement})
    return settlement


def lock_settlement(session: Session, tenant_id, settlement_id: uuid.UUID, *, actor: str = "system",
                    correlation_id: str | None = None) -> PartnerSettlement:
    request_id = correlation(correlation_id)
    settlement = get_settlement_or_404(session, tenant_id, settlement_id)
    _transition(settlement, "LOCKED")
    settlement.locked_at = _now()
    session.flush()
    audit(session, tenant_id, actor, "settlement.locked", resource_type="settlement",
          resource_id=settlement.id, correlation_id=request_id)
    outbox(session, "tenancy.settlement.locked.v1", tenant_id, request_id,
           {"tenant_id": str(tenant_id), "settlement_id": str(settlement.id)})
    return settlement


def generate_statement(session: Session, tenant_id, settlement_id: uuid.UUID, *,
                       actor: str = "system") -> PartnerStatement:
    settlement = get_settlement_or_404(session, tenant_id, settlement_id)
    if settlement.state not in ("LOCKED", "APPROVED", "PAYOUT_PENDING", "PAID"):
        raise ValidationError("statement requires a locked settlement")
    lines = list(session.scalars(select(SettlementLine).where(
        SettlementLine.settlement_id == settlement.id)))
    statement = PartnerStatement(tenant_id=tenant_id, settlement_id=settlement.id,
                                 partner_id=settlement.partner_id,
                                 statement_data={
                                     "net": settlement.net_settlement,
                                     "earnings": settlement.total_earnings,
                                     "clawbacks": settlement.total_clawbacks,
                                     "adjustments": settlement.total_adjustments,
                                     "withholding": settlement.withholding,
                                     "lines": [{"source": l.source_event_id, "type": l.line_type,
                                                "amount": l.amount, "state": l.state} for l in lines],
                                     "generated_by": actor,
                                 }, generated_at=_now())
    session.add(statement)
    session.flush()
    return statement


def record_payout(session: Session, tenant_id, settlement_id: uuid.UUID, *, amount: float,
                  method: str = "BANK_TRANSFER", reference: str | None = None,
                  recorded_by: str = "system", correlation_id: str | None = None) -> SettlementPayout:
    request_id = correlation(correlation_id)
    settlement = get_settlement_or_404(session, tenant_id, settlement_id)
    if settlement.state in ("LOCKED", "APPROVED"):
        _transition(settlement, "PAYOUT_PENDING")
    payout = SettlementPayout(tenant_id=tenant_id, settlement_id=settlement.id, amount=amount,
                              currency=settlement.currency, method=method, reference=reference,
                              recorded_by=recorded_by, paid_at=_now())
    session.add(payout)
    _transition(settlement, "PAID")
    settlement.payout_ref = reference or f"payout:{payout.id}"
    session.flush()
    # Post a balanced ledger entry: partner_payable -> wallet_cash.
    post_entry(session, tenant_id, entry_type="SETTLEMENT_PAYOUT",
               lines=[{"account": "partner_payable", "debit": amount, "credit": 0},
                      {"account": "wallet_cash", "debit": 0, "credit": amount}],
               reference=f"settlement:{settlement.id}", posted_by=recorded_by, correlation_id=request_id)
    audit(session, tenant_id, recorded_by, "settlement.payout", resource_type="settlement",
          resource_id=settlement.id, after={"amount": amount, "method": method}, correlation_id=request_id)
    outbox(session, "tenancy.settlement.paid.v1", tenant_id, request_id,
           {"tenant_id": str(tenant_id), "settlement_id": str(settlement.id), "amount": amount})
    return payout


def reconcile_settlement(session: Session, tenant_id, settlement_id: uuid.UUID, *, detail: dict | None = None,
                         reconciled_by: str = "system") -> SettlementReconciliation:
    settlement = get_settlement_or_404(session, tenant_id, settlement_id)
    _transition(settlement, "RECONCILING")
    row = SettlementReconciliation(tenant_id=tenant_id, settlement_id=settlement.id,
                                   state="RECONCILED", detail=detail or {}, reconciled_by=reconciled_by,
                                   reconciled_at=_now())
    session.add(row)
    _transition(settlement, "RECONCILED")
    session.flush()
    return row


def open_dispute(session: Session, tenant_id, settlement_id: uuid.UUID, *, line_id: uuid.UUID | None,
                 reason: str, submitted_by: str, evidence: list | None = None) -> SettlementDispute:
    settlement = get_settlement_or_404(session, tenant_id, settlement_id)
    if settlement.state == "LOCKED":
        raise SettlementLockedError("cannot dispute a locked settlement directly; request review")
    if line_id is not None:
        line = session.get(SettlementLine, line_id)
        if line is None or line.settlement_id != settlement.id:
            raise NotFoundError("settlement line not found")
        line.state = "DISPUTED"
    dispute = SettlementDispute(tenant_id=tenant_id, settlement_id=settlement.id, line_id=line_id,
                                state="OPEN", reason=reason, evidence_ref=evidence or [],
                                submitted_by=submitted_by)
    session.add(dispute)
    try:
        settlement_transition(settlement.state, "DISPUTED")
        settlement.state = "DISPUTED"
    except ValueError:
        pass
    session.flush()
    return dispute


def resolve_dispute(session: Session, tenant_id, dispute_id: uuid.UUID, *, resolution: str,
                    adjustment_ref: str | None = None, response: str | None = None,
                    resolved_by: str = "system") -> SettlementDispute:
    dispute = session.get(SettlementDispute, dispute_id)
    if dispute is None or dispute.tenant_id != tenant_id:
        raise NotFoundError("dispute not found")
    dispute.state = "RESOLVED"
    dispute.resolution = resolution
    dispute.response = response
    dispute.adjustment_ref = adjustment_ref
    dispute.resolved_by = resolved_by if hasattr(dispute, "resolved_by") else None
    session.flush()
    return dispute


def reverse_settlement(session: Session, tenant_id, settlement_id: uuid.UUID, *, reason: str,
                       reversed_by: str = "system", correlation_id: str | None = None) -> PartnerSettlement:
    request_id = correlation(correlation_id)
    settlement = get_settlement_or_404(session, tenant_id, settlement_id)
    if settlement.state not in ("LOCKED", "PAYOUT_PENDING", "PARTIALLY_PAID", "PAID", "DISPUTED"):
        raise ValidationError(f"cannot reverse settlement in state {settlement.state}")
    _transition(settlement, "REVERSED")
    settlement.failure_detail = reason if hasattr(settlement, "failure_detail") else None
    audit(session, tenant_id, reversed_by, "settlement.reversed", resource_type="settlement",
          resource_id=settlement.id, after={"state": "REVERSED"}, reason=reason, correlation_id=request_id)
    return settlement
