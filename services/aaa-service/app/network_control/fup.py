"""Fair Usage Policy (FUP) as a first-class policy.

Usage is aggregated from the authoritative AAA accounting projection
(`UsageProjection`). FUP counters are idempotent per (tenant, subscriber,
cycle); threshold crossing persists the tier once, throttles through CoA and
publishes the enforcement outcome. Reset and top-up restore normal policy."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import EnforcementAction, FupCounter, UsageProjection
from ..services import audit, correlation, outbox


def _now() -> datetime:
    return datetime.now(timezone.utc)


def cycle_key(fup: Any, now: datetime | None = None) -> str:
    now = now or _now()
    if getattr(fup, "cycle", "monthly") == "daily":
        return now.strftime("%Y-%m-%d")
    return now.strftime("%Y-%m")


def usage_bytes(session: Session, tenant_id, subscriber_id, period: str) -> tuple[int, int]:
    usage = session.scalar(
        select(UsageProjection).where(
            UsageProjection.tenant_id == tenant_id,
            UsageProjection.subscriber_id == subscriber_id,
            UsageProjection.period == period,
        )
    )
    if usage is None:
        return 0, 0
    return int(usage.input_octets or 0), int(usage.output_octets or 0)


def active_throttle_tier(fup: Any, input_octets: int, output_octets: int, topup_bytes: int = 0) -> dict | None:
    """Deterministic highest crossed tier; returns a policy layer dict or None."""
    thresholds = sorted(getattr(fup, "thresholds", []) or [], key=lambda item: int(item.get("limit_bytes", 0)))
    active: dict | None = None
    for threshold in thresholds:
        limit = int(threshold.get("limit_bytes", 0)) + int(topup_bytes)
        combined = bool(threshold.get("combined", True))
        used = (input_octets + output_octets) if combined else max(input_octets, output_octets)
        grace = int(getattr(fup, "grace_bytes", 0) or 0)
        if used > limit + grace:
            active = {
                "label": threshold.get("label", "tier"),
                "upload_kbps": threshold.get("upload_kbps"),
                "download_kbps": threshold.get("download_kbps"),
            }
    return active


def evaluate_fup(session: Session, tenant_id, subscriber_id, fup, now: datetime | None = None) -> dict | None:
    """Return the active FUP throttle tier (policy layer) or None."""
    if fup is None:
        return None
    period = cycle_key(fup, now)
    input_octets, output_octets = usage_bytes(session, tenant_id, subscriber_id, period)
    counter = session.scalar(
        select(FupCounter).where(FupCounter.tenant_id == tenant_id, FupCounter.subscriber_id == subscriber_id, FupCounter.cycle == period)
    )
    topup = int(counter.topup_bytes) if counter else 0
    return active_throttle_tier(fup, input_octets, output_octets, topup)


def record_threshold_event(
    session: Session,
    tenant_id,
    subscriber_id,
    fup,
    tier: dict,
    *,
    trigger: str = "fup_threshold_crossed",
    actor: str = "fup-worker",
) -> FupCounter:
    """Idempotently persist the crossed tier and queue a CoA throttle action."""
    period = cycle_key(fup)
    counter = session.scalar(
        select(FupCounter).where(FupCounter.tenant_id == tenant_id, FupCounter.subscriber_id == subscriber_id, FupCounter.cycle == period)
    )
    if counter is None:
        counter = FupCounter(tenant_id=tenant_id, subscriber_id=subscriber_id, cycle=period)
        session.add(counter)
        session.flush()  # visible to subsequent idempotency checks
    if counter.active_tier == tier.get("label") and counter.throttled:
        return counter  # already applied — idempotent
    counter.active_tier = tier.get("label")
    counter.throttled = True
    counter.last_event_at = _now()
    request_id = correlation(None)
    idem = f"fup:{tenant_id}:{subscriber_id}:{period}:{tier.get('label')}"
    existing = session.scalar(select(EnforcementAction).where(EnforcementAction.tenant_id == tenant_id, EnforcementAction.idempotency_key == idem))
    if existing is None:
        session.add(
            EnforcementAction(
                tenant_id=tenant_id,
                action_type="COA",
                trigger=trigger,
                idempotency_key=idem,
                subscriber_id=subscriber_id,
                status="PENDING",
                target="subscriber",
                attributes={"fup_tier": tier.get("label"), "upload_kbps": tier.get("upload_kbps"), "download_kbps": tier.get("download_kbps")},
                correlation_id=request_id,
                actor=actor,
            )
        )
        session.flush()
    outbox(session, "fup.threshold_reached.v1", tenant_id, request_id, {"subscriber_id": str(subscriber_id), "period": period, "tier": tier.get("label"), "upload_kbps": tier.get("upload_kbps"), "download_kbps": tier.get("download_kbps")}, idem)
    audit(session, tenant_id, "fup.threshold_crossed", str(subscriber_id), request_id, {"period": period, "tier": tier.get("label")})
    return counter


def reset_cycle(session: Session, tenant_id, subscriber_id, fup, *, actor: str = "fup-worker") -> FupCounter:
    """Reset the FUP counter for the current cycle and restore normal policy."""
    period = cycle_key(fup)
    counter = session.scalar(
        select(FupCounter).where(FupCounter.tenant_id == tenant_id, FupCounter.subscriber_id == subscriber_id, FupCounter.cycle == period)
    )
    if counter is None:
        counter = FupCounter(tenant_id=tenant_id, subscriber_id=subscriber_id, cycle=period)
        session.add(counter)
    was_throttled = counter.throttled
    counter.input_octets = 0
    counter.output_octets = 0
    counter.topup_bytes = 0
    counter.active_tier = None
    counter.throttled = False
    counter.last_event_at = _now()
    request_id = correlation(None)
    if was_throttled:
        outbox(session, "fup.restriction_removed.v1", tenant_id, request_id, {"subscriber_id": str(subscriber_id), "period": period}, f"fup-reset:{tenant_id}:{subscriber_id}:{period}")
        audit(session, tenant_id, "fup.cycle_reset", str(subscriber_id), request_id, {"period": period})
    return counter


def apply_topup(session: Session, tenant_id, subscriber_id, fup, topup_bytes: int, *, actor: str = "topup") -> FupCounter:
    """Grant a purchased top-up (raises the effective FUP limit) and, if a
    throttle was active below the new limit, remove it through CoA."""
    if topup_bytes <= 0:
        raise ValueError("topup_bytes must be positive")
    period = cycle_key(fup)
    counter = session.scalar(
        select(FupCounter).where(FupCounter.tenant_id == tenant_id, FupCounter.subscriber_id == subscriber_id, FupCounter.cycle == period)
    )
    if counter is None:
        counter = FupCounter(tenant_id=tenant_id, subscriber_id=subscriber_id, cycle=period)
        session.add(counter)
    counter.topup_bytes = int(counter.topup_bytes or 0) + topup_bytes
    counter.last_event_at = _now()
    request_id = correlation(None)
    input_octets, output_octets = usage_bytes(session, tenant_id, subscriber_id, period)
    active = active_throttle_tier(fup, input_octets, output_octets, counter.topup_bytes)
    if active is None and counter.throttled:
        # Top-up cleared the throttle — restore normal policy.
        counter.active_tier = None
        counter.throttled = False
        outbox(session, "fup.restriction_removed.v1", tenant_id, request_id, {"subscriber_id": str(subscriber_id), "period": period, "reason": "topup"}, f"fup-topup-remove:{tenant_id}:{subscriber_id}:{period}")
    audit(session, tenant_id, "fup.topup_applied", str(subscriber_id), request_id, {"topup_bytes": topup_bytes, "period": period})
    return counter
