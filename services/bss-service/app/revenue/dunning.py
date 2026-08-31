"""Dunning engine: versioned policies, stages, cases, actions, holds.

Suspension is never performed by BSS directly — the engine publishes
`billing.suspension_required.v1` and OSS creates the order. Restoration is
driven by `billing.restoration_eligible.v1` after a payment clears."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .events import publish_outbox
from .models import (
    BillingAccount,
    CollectionHold,
    DunningAction,
    DunningCase,
    DunningPolicy,
    DunningPolicyVersion,
    DunningStage,
    PromiseToPay,
    RevenueInvoice,
)
from .state_machine import dunning_case_transition


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(value: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes for timezone columns; assume UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def create_dunning_policy(session: Session, tenant_id, *, code: str, name: str, params: dict) -> DunningPolicy:
    policy = DunningPolicy(tenant_id=tenant_id, code=code, name=name, state="DRAFT")
    session.add(policy)
    session.flush()
    version = DunningPolicyVersion(tenant_id=tenant_id, policy_id=policy.id, version=1, state="DRAFT", params=params)
    session.add(version)
    session.flush()
    policy.current_version_id = version.id
    return policy


def add_dunning_stage(session: Session, tenant_id, policy_version_id, *, stage_order: int, stage_code: str, delay_seconds: int, action_type: str, message_template: str | None = None) -> DunningStage:
    version = session.scalar(select(DunningPolicyVersion).where(DunningPolicyVersion.id == policy_version_id, DunningPolicyVersion.tenant_id == tenant_id))
    if version is None:
        raise ValueError("dunning policy version not found")
    if version.state != "DRAFT":
        raise ValueError("published dunning policy versions are immutable; create a new version")
    stage = DunningStage(
        tenant_id=tenant_id,
        policy_version_id=version.id,
        stage_order=stage_order,
        stage_code=stage_code,
        delay_seconds=delay_seconds,
        action_type=action_type,
        message_template=message_template,
    )
    session.add(stage)
    session.flush()
    return stage


def publish_dunning_policy(session: Session, tenant_id, policy_id) -> DunningPolicyVersion:
    policy = session.scalar(select(DunningPolicy).where(DunningPolicy.id == policy_id, DunningPolicy.tenant_id == tenant_id))
    if policy is None:
        raise ValueError("dunning policy not found")
    version = session.get(DunningPolicyVersion, policy.current_version_id)
    if version is None:
        raise ValueError("dunning policy has no version")
    if not session.scalar(select(DunningStage).where(DunningStage.policy_version_id == version.id).limit(1)):
        raise ValueError("dunning policy has no stages")
    version.state = "ACTIVE"
    policy.state = "ACTIVE"
    session.flush()
    return version


def account_overdue(session: Session, tenant_id, billing_account_id, now: datetime | None = None) -> Decimal:
    now = now or _now()
    invoices = list(
        session.scalars(
            select(RevenueInvoice).where(
                RevenueInvoice.tenant_id == tenant_id,
                RevenueInvoice.billing_account_id == billing_account_id,
                RevenueInvoice.status.in_(["ISSUED", "PARTIALLY_PAID", "OVERDUE"]),
                RevenueInvoice.due_date < now,
            )
        )
    )
    return sum((inv.total_amount - inv.paid_amount - inv.written_off_amount for inv in invoices), Decimal("0.00"))


def open_dunning_case(session: Session, tenant_id, billing_account_id, policy_version_id, *, correlation_id: str) -> DunningCase:
    existing = session.scalar(select(DunningCase).where(DunningCase.tenant_id == tenant_id, DunningCase.billing_account_id == billing_account_id, DunningCase.policy_version_id == policy_version_id))
    if existing is not None and existing.status in ("OPEN", "PAUSED"):
        return existing
    overdue = account_overdue(session, tenant_id, billing_account_id)
    if overdue <= 0:
        raise ValueError("billing account is not overdue")
    case = DunningCase(
        tenant_id=tenant_id,
        billing_account_id=billing_account_id,
        policy_version_id=policy_version_id,
        status="OPEN",
        current_stage_order=0,
        next_due_at=_now(),
    )
    session.add(case)
    session.flush()
    from .events import publish_outbox

    publish_outbox(session, "billing.account_delinquent.v1", {"billing_account_id": str(billing_account_id), "overdue": str(overdue)}, tenant_id, correlation_id, f"dunning-open:{tenant_id}:{billing_account_id}")
    return case


def advance_dunning_case(session: Session, tenant_id, case_id, *, correlation_id: str) -> DunningCase:
    """Run the next due dunning stage (idempotent, schedule-aware)."""
    case = session.scalar(select(DunningCase).where(DunningCase.id == case_id, DunningCase.tenant_id == tenant_id))
    if case is None:
        raise ValueError("dunning case not found")
    if case.status != "OPEN" or case.suspended:
        return case
    if _as_aware(case.next_due_at) and _as_aware(case.next_due_at) > _now():
        return case  # not due yet
    stages = list(session.scalars(select(DunningStage).where(DunningStage.policy_version_id == case.policy_version_id).order_by(DunningStage.stage_order)))
    if not stages:
        return case
    stage = next((s for s in stages if s.stage_order > case.current_stage_order), None)
    if stage is None:
        return case  # no further stages
    case.current_stage_order = stage.stage_order
    action = DunningAction(
        tenant_id=tenant_id,
        case_id=case.id,
        stage_order=stage.stage_order,
        action_type=stage.action_type,
        trigger="scheduled",
        result={"stage_code": stage.stage_code, "message_template": stage.message_template},
        correlation_id=correlation_id,
    )
    session.add(action)
    if stage.action_type in ("SUSPEND", "RESTRICT"):
        _publish_suspension(session, tenant_id, case, stage, correlation_id)
        if stage.action_type == "SUSPEND":
            case.status = dunning_case_transition(case.status, "CLOSED")
            case.resolved_at = _now()
    publish_outbox(session, "dunning.stage_changed.v1", {"case_id": str(case.id), "stage": stage.stage_code, "action_type": stage.action_type}, tenant_id, correlation_id, f"dunning-stage:{case.id}:{stage.stage_order}")
    # Schedule the next stage.
    next_stage = next((s for s in stages if s.stage_order > stage.stage_order), None)
    if next_stage and case.status == "OPEN":
        case.next_due_at = _now() + timedelta(seconds=next_stage.delay_seconds)
    else:
        case.next_due_at = None
    session.flush()
    return case


def _publish_suspension(session: Session, tenant_id, case: DunningCase, stage: DunningStage, correlation_id: str) -> None:
    """Publish the suspension-required event. OSS creates the idempotent
    suspension order; BSS never touches RouterOS/AAA directly."""
    account = session.scalar(select(BillingAccount).where(BillingAccount.id == case.billing_account_id))
    publish_outbox(
        session,
        "billing.suspension_required.v1",
        {
            "billing_account_id": str(case.billing_account_id),
            "customer_ref": account.customer_ref if account else None,
            "stage": stage.stage_code,
            "scope": "all_services",
            "reason": "financial delinquency",
            "correlation_id": correlation_id,
        },
        tenant_id,
        correlation_id,
        f"suspend-required:{tenant_id}:{case.billing_account_id}:{stage.stage_order}",
    )


def pause_dunning_case(session: Session, tenant_id, case_id, *, actor: str) -> DunningCase:
    case = session.scalar(select(DunningCase).where(DunningCase.id == case_id, DunningCase.tenant_id == tenant_id))
    if case is None:
        raise ValueError("dunning case not found")
    case.status = dunning_case_transition(case.status, "PAUSED")
    case.suspended = True
    return case


def resume_dunning_case(session: Session, tenant_id, case_id, *, actor: str) -> DunningCase:
    case = session.scalar(select(DunningCase).where(DunningCase.id == case_id, DunningCase.tenant_id == tenant_id))
    if case is None:
        raise ValueError("dunning case not found")
    case.status = dunning_case_transition(case.status, "OPEN")
    case.suspended = False
    case.next_due_at = _now()
    return case


def resolve_dunning_case(session: Session, tenant_id, case_id, *, correlation_id: str) -> DunningCase:
    case = session.scalar(select(DunningCase).where(DunningCase.id == case_id, DunningCase.tenant_id == tenant_id))
    if case is None:
        raise ValueError("dunning case not found")
    case.status = dunning_case_transition(case.status, "RESOLVED")
    case.resolved_at = _now()
    publish_outbox(session, "dunning.case_resolved.v1", {"case_id": str(case.id), "billing_account_id": str(case.billing_account_id)}, tenant_id, correlation_id, f"dunning-resolve:{case.id}")
    session.flush()
    return case


def record_promise_to_pay(session: Session, tenant_id, billing_account_id, *, amount, currency, promise_date, created_by) -> PromiseToPay:
    promise = PromiseToPay(tenant_id=tenant_id, billing_account_id=billing_account_id, amount=amount, currency=currency, promise_date=promise_date, status="ACTIVE", created_by=created_by)
    session.add(promise)
    session.flush()
    return promise


def place_collection_hold(session: Session, tenant_id, billing_account_id, *, kind: str, reason: str, created_by) -> CollectionHold:
    existing = session.scalar(select(CollectionHold).where(CollectionHold.tenant_id == tenant_id, CollectionHold.billing_account_id == billing_account_id, CollectionHold.kind == kind, CollectionHold.status == "ACTIVE"))
    if existing is not None:
        return existing
    hold = CollectionHold(tenant_id=tenant_id, billing_account_id=billing_account_id, kind=kind, reason=reason, status="ACTIVE", created_by=created_by)
    session.add(hold)
    session.flush()
    return hold


def remove_collection_hold(session: Session, tenant_id, hold_id, *, actor: str) -> CollectionHold:
    hold = session.scalar(select(CollectionHold).where(CollectionHold.id == hold_id, CollectionHold.tenant_id == tenant_id))
    if hold is None:
        raise ValueError("collection hold not found")
    hold.status = "RELEASED"
    return hold
