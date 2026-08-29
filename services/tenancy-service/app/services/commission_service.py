"""Commission engine: versioned plans/rules, partner agreements, deterministic
earnings, clawbacks and adjustments. Earnings are immutable; reversals are new
rows. Every earning is reproducible from its source event."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain import commissions as commission_rules
from ..domain.exceptions import (
    CommissionError,
    DuplicateError,
    NotFoundError,
    ValidationError,
)
from ..events import outbox
from ..models import (
    CommissionAdjustment,
    CommissionAgreement,
    CommissionClawback,
    CommissionEarning,
    CommissionPlan,
    CommissionPlanVersion,
    CommissionRule,
    RevenueShareRule,
)
from ..state_machine import guarded
from .audit_service import audit, correlation
from .organization_service import get_partner_or_404


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_plan_or_404(session: Session, tenant_id, plan_id) -> CommissionPlan:
    plan = session.get(CommissionPlan, plan_id)
    if plan is None or plan.tenant_id != tenant_id:
        raise NotFoundError("commission plan not found")
    return plan


# ---------------------------------------------------------------------------
# Plans + rules
# ---------------------------------------------------------------------------
def create_plan(session: Session, tenant_id, *, code: str, name: str,
                actor: str = "system") -> CommissionPlan:
    existing = session.scalars(select(CommissionPlan).where(
        CommissionPlan.tenant_id == tenant_id, CommissionPlan.code == code)).first()
    if existing is not None:
        raise DuplicateError(f"commission plan {code!r} already exists")
    plan = CommissionPlan(tenant_id=tenant_id, code=code, name=name)
    session.add(plan)
    session.flush()
    session.add(CommissionPlanVersion(tenant_id=tenant_id, plan_id=plan.id, version=1))
    return plan


def approve_plan(session: Session, tenant_id, plan_id: uuid.UUID, *, approved_by: str,
                 correlation_id: str | None = None) -> CommissionPlan:
    plan = get_plan_or_404(session, tenant_id, plan_id)
    plan.status = "APPROVED"
    plan.approved_by = approved_by
    plan.approved_at = _now()
    session.flush()
    audit(session, tenant_id, approved_by, "commission.plan.approved", resource_type="commission_plan",
          resource_id=plan.id, after={"status": "APPROVED"}, correlation_id=correlation(None))
    return plan


def add_rule(session: Session, tenant_id, plan_id: uuid.UUID, *, code: str, name: str, basis: str,
             calculation_type: str, rate: float | None = None, fixed_amount: float | None = None,
             currency: str = "INR", tiers: list | None = None, slabs: list | None = None,
             exclusions: list | None = None, threshold: float | None = None,
             multiplier: float = 1.0, actor: str = "system") -> CommissionRule:
    plan = get_plan_or_404(session, tenant_id, plan_id)
    rule_dict = {"calculation_type": calculation_type, "basis": basis, "rate": rate,
                 "fixed_amount": fixed_amount, "exclusions": exclusions}
    errors = commission_rules.validate_rule(rule_dict)
    if errors:
        raise ValidationError("; ".join(errors))
    existing = session.scalars(select(CommissionRule).where(
        CommissionRule.plan_id == plan.id, CommissionRule.code == code)).first()
    if existing is not None:
        raise DuplicateError(f"rule {code!r} already exists in plan")
    rule = CommissionRule(tenant_id=tenant_id, plan_id=plan.id, code=code, name=name, basis=basis,
                          calculation_type=calculation_type, rate=rate, fixed_amount=fixed_amount,
                          currency=currency, tiers=tiers or [], slabs=slabs or [],
                          exclusions=exclusions or [], threshold=threshold, multiplier=multiplier)
    session.add(rule)
    session.flush()
    version = session.scalars(select(CommissionPlanVersion).where(
        CommissionPlanVersion.plan_id == plan.id).order_by(
        CommissionPlanVersion.version.desc()).limit(1)).first()
    session.add(CommissionPlanVersion(tenant_id=tenant_id, plan_id=plan.id,
                                      version=(version.version + 1 if version else 1)))
    return rule


def create_agreement(session: Session, tenant_id, *, partner_id: uuid.UUID, plan_id: uuid.UUID,
                     actor: str = "system") -> CommissionAgreement:
    get_partner_or_404(session, tenant_id, partner_id)
    plan = get_plan_or_404(session, tenant_id, plan_id)
    if plan.status != "APPROVED":
        raise CommissionError("commission plan must be approved before agreement")
    existing = session.scalars(select(CommissionAgreement).where(
        CommissionAgreement.tenant_id == tenant_id, CommissionAgreement.partner_id == partner_id,
        CommissionAgreement.plan_id == plan.id)).first()
    if existing is not None:
        return existing
    version = session.scalars(select(CommissionPlanVersion).where(
        CommissionPlanVersion.plan_id == plan.id).order_by(
        CommissionPlanVersion.version.desc()).limit(1)).first()
    agreement = CommissionAgreement(tenant_id=tenant_id, partner_id=partner_id, plan_id=plan.id,
                                    plan_version_id=version.id if version else None)
    session.add(agreement)
    session.flush()
    return agreement


def _rules_for_agreement(session: Session, agreement: CommissionAgreement):
    return list(session.scalars(select(CommissionRule).where(
        CommissionRule.plan_id == agreement.plan_id, CommissionRule.is_active.is_(True))))


# ---------------------------------------------------------------------------
# Earnings
# ---------------------------------------------------------------------------
def recognize_earning(session: Session, tenant_id, *, partner_id: uuid.UUID,
                      source_event_id: str, source_event_type: str, basis: str,
                      basis_amount: float, customer_id: str | None = None,
                      service_id: str | None = None, invoice_ref: str | None = None,
                      payment_ref: str | None = None, currency: str = "INR",
                      actor: str = "system", correlation_id: str | None = None) -> CommissionEarning:
    """Recognize a commission earning from a basis event. Idempotent per
    (tenant, source_event_id, rule). Deterministic and explainable."""
    request_id = correlation(correlation_id)
    partner = get_partner_or_404(session, tenant_id, partner_id)
    agreement = session.scalars(select(CommissionAgreement).where(
        CommissionAgreement.tenant_id == tenant_id,
        CommissionAgreement.partner_id == partner_id)).first()
    if agreement is None:
        raise CommissionError("no commission agreement for partner")
    rules = _rules_for_agreement(session, agreement)
    matching = [r for r in rules if r.basis == basis]
    if not matching:
        raise CommissionError(f"no active rule for basis {basis!r}")
    rule = matching[0]
    existing = session.scalars(select(CommissionEarning).where(
        CommissionEarning.tenant_id == tenant_id,
        CommissionEarning.source_event_id == source_event_id,
        CommissionEarning.rule_id == rule.id)).first()
    if existing is not None:
        return existing
    result = commission_rules.calculate(
        {"calculation_type": rule.calculation_type, "rate": rule.rate,
         "fixed_amount": rule.fixed_amount, "tiers": rule.tiers, "slabs": rule.slabs,
         "threshold": rule.threshold, "multiplier": rule.multiplier},
        basis_amount=Decimal(str(basis_amount)))
    earning = CommissionEarning(
        tenant_id=tenant_id, partner_id=partner.id, agreement_id=agreement.id, rule_id=rule.id,
        rule_version=agreement.plan_version_id and 1,
        source_event_id=source_event_id, source_event_type=source_event_type,
        customer_id=customer_id, service_id=service_id, invoice_ref=invoice_ref,
        payment_ref=payment_ref, basis=basis, basis_amount=float(basis_amount),
        rate_formula=result["formula"], amount=float(result["amount"]), currency=currency,
        explanation=result["explanation"], recognized_at=_now(), correlation_id=request_id)
    session.add(earning)
    session.flush()
    audit(session, tenant_id, actor, "commission.earning.recognized", resource_type="commission_earning",
          resource_id=earning.id, after={"amount": float(result["amount"]), "basis": basis,
                                         "formula": result["formula"]}, correlation_id=request_id)
    outbox(session, "tenancy.commission.earning.v1", tenant_id, request_id,
           {"tenant_id": str(tenant_id), "earning_id": str(earning.id), "partner_id": str(partner.id),
            "amount": float(result["amount"]), "currency": currency})
    return earning


def clawback_earning(session: Session, tenant_id, earning_id: uuid.UUID, *, amount: float | None,
                     kind: str, source_event_id: str, reason: str | None = None,
                     actor: str = "system", correlation_id: str | None = None) -> CommissionClawback:
    """Create an immutable clawback; the original earning is never deleted."""
    request_id = correlation(correlation_id)
    earning = session.get(CommissionEarning, earning_id)
    if earning is None or earning.tenant_id != tenant_id:
        raise NotFoundError("commission earning not found")
    clawback_amount = amount if amount is not None else earning.amount
    existing = session.scalars(select(CommissionClawback).where(
        CommissionClawback.tenant_id == tenant_id, CommissionClawback.earning_id == earning.id,
        CommissionClawback.source_event_id == source_event_id)).first()
    if existing is not None:
        return existing
    clawback = CommissionClawback(tenant_id=tenant_id, earning_id=earning.id, amount=clawback_amount,
                                  kind=kind, source_event_id=source_event_id, reason=reason,
                                  actor=actor, correlation_id=request_id)
    session.add(clawback)
    earning.status = "CLAWED_BACK"
    session.flush()
    audit(session, tenant_id, actor, "commission.clawback", resource_type="commission_earning",
          resource_id=earning.id, after={"amount": clawback_amount, "kind": kind}, reason=reason,
          correlation_id=request_id)
    outbox(session, "tenancy.commission.clawback.v1", tenant_id, request_id,
           {"tenant_id": str(tenant_id), "earning_id": str(earning.id),
            "amount": clawback_amount, "kind": kind})
    return clawback


def adjust_earning(session: Session, tenant_id, earning_id: uuid.UUID, *, amount: float, kind: str,
                   reason: str | None, actor: str = "system") -> CommissionAdjustment:
    earning = session.get(CommissionEarning, earning_id)
    if earning is None or earning.tenant_id != tenant_id:
        raise NotFoundError("commission earning not found")
    adjustment = CommissionAdjustment(tenant_id=tenant_id, earning_id=earning.id, kind=kind,
                                      amount=amount, reason=reason, actor=actor)
    session.add(adjustment)
    earning.status = "ADJUSTED"
    session.flush()
    audit(session, tenant_id, actor, "commission.adjustment", resource_type="commission_earning",
          resource_id=earning.id, after={"amount": amount, "kind": kind}, reason=reason)
    return adjustment
