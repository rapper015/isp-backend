"""SLO lifecycle: versions, measurements, error budgets, maintenance handling."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.exceptions import NotFoundError, SloError, SloImmutableError
from ..domain.slos import calculate_error_budget, window_bounds, with_maintenance_excluded
from ..events import envelope, outbox
from ..models import (MaintenanceException, MaintenanceWindow, SlIDefinition, SlIMeasurement,
                      SloDefinition, SloVersion, SloWindowState)
from ..state_machine import guarded  # noqa: F401

# SLO lifecycle transitions (explicit, no silent jumps)
_SLO_FLOW = {
    "DRAFT": {"VALIDATING", "ARCHIVED"},
    "VALIDATING": {"APPROVED", "DRAFT", "ARCHIVED"},
    "APPROVED": {"ACTIVE", "DISABLED", "ARCHIVED"},
    "ACTIVE": {"SUPERSEDED", "DISABLED", "ARCHIVED"},
    "SUPERSEDED": {"ARCHIVED"},
    "DISABLED": {"ACTIVE", "ARCHIVED"},
    "ARCHIVED": set(),
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _slo_transition(slo: SloDefinition, target: str):
    if target not in _SLO_FLOW.get(slo.state, set()):
        raise SloError(f"invalid SLO transition {slo.state} -> {target}")
    slo.state = target
    return slo


def list_services(session: Session, tenant_id, *, platform: bool = False, limit: int = 100):
    return list(session.execute(select(__import__("app.models", fromlist=["ServiceDefinition"]).ServiceDefinition)).scalars())[:limit]


def create_sli(session: Session, tenant_id, data: dict) -> SlIDefinition:
    existing = session.scalars(select(SlIDefinition).where(SlIDefinition.code == data["code"])).first()
    if existing is not None:
        raise SloError(f"SLI code {data['code']!r} already exists")
    sli = SlIDefinition(**{k: v for k, v in data.items() if k in {
        "code", "name", "service_id", "measurement_source", "good_event_definition",
        "valid_event_definition", "query_expression", "unit", "exclusions", "owner"}})
    session.add(sli)
    session.flush()
    return sli


def record_measurement(session: Session, tenant_id, sli_code: str, *, good: float, total: float,
                       window_start: datetime | None = None, window_end: datetime | None = None,
                       quality: str = "VALID", excluded_good: float = 0.0, excluded_total: float = 0.0,
                       source_ref: str | None = None) -> SlIMeasurement:
    sli = session.scalars(select(SlIDefinition).where(SlIDefinition.code == sli_code)).first()
    if sli is None:
        raise NotFoundError(f"SLI {sli_code!r} not found")
    now = _now()
    w_end = window_end or now
    w_start = window_start or (w_end - timedelta(minutes=5))
    row = SlIMeasurement(tenant_id=tenant_id, sli_id=sli.id, window_start=w_start, window_end=w_end,
                         good=good, total=total, quality=quality, excluded_good=excluded_good,
                         excluded_total=excluded_total, source_ref=source_ref, recorded_at=now)
    session.add(row)
    session.flush()
    return row


def create_slo(session: Session, tenant_id, data: dict) -> SloDefinition:
    sli = session.scalars(select(SlIDefinition).where(SlIDefinition.id == data.get("sli_id"))).first()
    if sli is None:
        raise NotFoundError("SLI not found")
    if session.scalars(select(SloDefinition).where(SloDefinition.code == data.get("code"))).first() is not None:
        raise SloError(f"SLO code {data.get('code')!r} already exists")
    slo = SloDefinition(code=data["code"], name=data.get("name", data["code"]),
                        service_id=data.get("service_id"), sli_id=sli.id,
                        state="DRAFT", owner=data.get("owner"))
    session.add(slo)
    session.flush()
    _new_version(session, slo, tenant_id, data)
    return slo


def _new_version(session: Session, slo: SloDefinition, tenant_id, data: dict) -> SloVersion:
    version = _next_version(session, slo.id)
    published = data.get("published", False)
    v = SloVersion(slo_id=slo.id, tenant_id=tenant_id, version=version,
                   objective=float(data["objective"]),
                   window_type=data.get("window_type", "ROLLING"),
                   window_seconds=int(data.get("window_seconds", 30 * 24 * 3600)),
                   service_tier=data.get("service_tier", "STANDARD"),
                   effective_from=parse_dt(data.get("effective_from")),
                   error_budget_policy=data.get("error_budget_policy", {}),
                   alert_thresholds=data.get("alert_thresholds", {}),
                   state="ACTIVE" if published else "DRAFT",
                   changed_by=data.get("changed_by"))
    session.add(v)
    session.flush()
    return v


def _next_version(session: Session, slo_id: uuid.UUID) -> int:
    last = session.execute(select(SloVersion).where(SloVersion.slo_id == slo_id)
                           .order_by(SloVersion.version.desc()).limit(1)).scalars().first()
    return (last.version + 1) if last else 1


def validate_slo(session: Session, slo_id: uuid.UUID) -> SloDefinition:
    slo = _get_slo(session, slo_id)
    return _slo_transition(slo, "VALIDATING")


def approve_slo(session: Session, slo_id: uuid.UUID, approved_by: str) -> SloDefinition:
    slo = _get_slo(session, slo_id)
    _slo_transition(slo, "APPROVED")
    slo.approved_by = approved_by
    slo.approved_at = _now()
    return slo


def activate_slo(session: Session, slo_id: uuid.UUID) -> SloDefinition:
    slo = _get_slo(session, slo_id)
    if slo.state not in ("APPROVED", "DISABLED"):
        raise SloError(f"cannot activate SLO in state {slo.state}")
    _slo_transition(slo, "ACTIVE")
    return slo


def _get_slo(session: Session, slo_id: uuid.UUID) -> SloDefinition:
    slo = session.scalars(select(SloDefinition).where(SloDefinition.id == slo_id)).first()
    if slo is None:
        raise NotFoundError("SLO not found")
    return slo


def latest_version(session: Session, slo_id: uuid.UUID) -> SloVersion:
    v = session.execute(select(SloVersion).where(SloVersion.slo_id == slo_id)
                        .order_by(SloVersion.version.desc()).limit(1)).scalars().first()
    if v is None:
        raise SloError("SLO has no version")
    return v


def compute_window(session: Session, tenant_id, slo_id: uuid.UUID, *, window_start: datetime,
                   window_end: datetime, force: bool = False) -> SloWindowState:
    slo = _get_slo(session, slo_id)
    version = latest_version(session, slo_id)
    existing = session.scalars(select(SloWindowState).where(
        SloWindowState.slo_id == slo_id,
        SloWindowState.window_start == window_start,
        SloWindowState.window_end == window_end)).first()
    if existing is not None and not force:
        return existing
    measurements = list(session.scalars(select(SlIMeasurement).where(
        SlIMeasurement.sli_id == slo.sli_id,
        SlIMeasurement.window_start >= window_start,
        SlIMeasurement.window_end <= window_end)))
    good = sum(m.good for m in measurements)
    total = sum(m.total for m in measurements)
    # Apply maintenance exclusions captured at measurement time (raw preserved).
    excluded_good = sum(m.excluded_good for m in measurements)
    excluded_total = sum(m.excluded_total for m in measurements)
    if excluded_total:
        good, total = with_maintenance_excluded(good, total, excluded_good, excluded_total)
    result = calculate_error_budget(good=good, total=total, objective=version.objective,
                                    window_seconds=version.window_seconds,
                                    policy_version=f"v{version.version}", window_type=version.window_type)
    if existing is None:
        row = SloWindowState(tenant_id=tenant_id, slo_id=slo.id, version=version.version,
                             window_start=window_start, window_end=window_end,
                             good=good, total=total, sli_ratio=result.sli_ratio,
                             objective=version.objective, allowed_bad=result.allowed_bad,
                             consumed_bad=result.consumed_bad, remaining_budget=result.remaining_budget,
                             burn_rate=result.burn_rate, status=result.status,
                             fast_burn=result.fast_burn, slow_burn=result.slow_burn,
                             computed_at=_now())
        session.add(row)
    else:
        existing.good, existing.total, existing.sli_ratio = good, total, result.sli_ratio
        existing.allowed_bad, existing.consumed_bad = result.allowed_bad, result.consumed_bad
        existing.remaining_budget = result.remaining_budget
        existing.burn_rate = result.burn_rate
        existing.status = result.status
        existing.fast_burn, existing.slow_burn = result.fast_burn, result.slow_burn
        existing.computed_at = _now()
        row = existing
    session.flush()
    return row


def error_budget(session: Session, slo_id: uuid.UUID, *, now: datetime | None = None) -> dict:
    slo = _get_slo(session, slo_id)
    version = latest_version(session, slo_id)
    now = now or _now()
    w_start, w_end = window_bounds(now, window_type=version.window_type, window_seconds=version.window_seconds)
    state = compute_window(session, None, slo_id, window_start=w_start, window_end=w_end)
    return {"slo_id": str(slo.id), "code": slo.code, "state": slo.state, "version": version.version,
            "objective": version.objective, "window_type": version.window_type,
            "window_start": w_start.isoformat(), "window_end": w_end.isoformat(),
            "sli_ratio": state.sli_ratio, "allowed_bad": state.allowed_bad,
            "consumed_bad": state.consumed_bad, "remaining_budget": state.remaining_budget,
            "burn_rate": state.burn_rate, "status": state.status,
            "fast_burn": state.fast_burn, "slow_burn": state.slow_burn}


def publish_slo_event(session: Session, slo: SloDefinition, correlation_id: str | None,
                      event_type: str, payload: dict | None = None) -> None:
    outbox(session, event_type, None, correlation_id, payload or {
        "slo_id": str(slo.id), "code": slo.code, "state": slo.state,
    }, idempotency_key=f"slo:{slo.id}:{event_type}")


def parse_dt(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
