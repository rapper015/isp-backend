"""Tenant dashboards + platform aggregates (require PLATFORM scope)."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..domain.exceptions import UnauthorizedAggregateError
from ..models import (Alert, Incident, KpiMeasurement, SlIMeasurement, SloWindowState,
                      SyntheticResult)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def tenant_dashboard(session: Session, tenant_id, *, hours: int = 24) -> dict:
    since = _now() - timedelta(hours=hours)
    firing = session.scalar(select(func.count(Alert.id)).where(
        Alert.tenant_id == tenant_id, Alert.state.in_(("FIRING", "ACKNOWLEDGED")))) or 0
    active_incidents = session.scalar(select(func.count(Incident.id)).where(
        Incident.tenant_id == tenant_id,
        Incident.state.in_(("DETECTED", "TRIAGE", "INVESTIGATING", "IDENTIFIED", "MITIGATING", "MONITORING")))) or 0
    sli_good = session.scalar(select(func.coalesce(func.sum(SlIMeasurement.good), 0.0)).where(
        SlIMeasurement.tenant_id == tenant_id, SlIMeasurement.window_end >= since)) or 0.0
    sli_total = session.scalar(select(func.coalesce(func.sum(SlIMeasurement.total), 0.0)).where(
        SlIMeasurement.tenant_id == tenant_id, SlIMeasurement.window_end >= since)) or 0.0
    synthetic_pass = session.scalar(select(func.count(SyntheticResult.id)).where(
        SyntheticResult.tenant_id == tenant_id, SyntheticResult.result == "PASS",
        SyntheticResult.checked_at >= since)) or 0
    synthetic_total = session.scalar(select(func.count(SyntheticResult.id)).where(
        SyntheticResult.tenant_id == tenant_id, SyntheticResult.checked_at >= since)) or 0
    return {
        "tenant_id": str(tenant_id) if tenant_id else None,
        "window_hours": hours,
        "firing_alerts": firing,
        "active_incidents": active_incidents,
        "sli_good": sli_good,
        "sli_total": sli_total,
        "sli_ratio": (sli_good / sli_total) if sli_total else 1.0,
        "synthetic_pass": synthetic_pass,
        "synthetic_total": synthetic_total,
        "synthetic_availability": (synthetic_pass / synthetic_total) if synthetic_total else 1.0,
        "is_platform_aggregate": False,
    }


def platform_aggregate(session: Session, *, hours: int = 24) -> dict:
    """Cross-tenant aggregate. Requires PLATFORM scope authorization."""
    since = _now() - timedelta(hours=hours)
    total_alerts = session.scalar(select(func.count(Alert.id)).where(Alert.last_observed >= since)) or 0
    firing = session.scalar(select(func.count(Alert.id)).where(
        Alert.state.in_(("FIRING", "ACKNOWLEDGED")))) or 0
    total_incidents = session.scalar(select(func.count(Incident.id)).where(
        Incident.detected_at >= since)) or 0
    major_incidents = session.scalar(select(func.count(Incident.id)).where(
        Incident.is_major.is_(True), Incident.detected_at >= since)) or 0
    active_incidents = session.scalar(select(func.count(Incident.id)).where(
        Incident.state.in_(("DETECTED", "TRIAGE", "INVESTIGATING", "IDENTIFIED", "MITIGATING", "MONITORING")))) or 0
    tenants_with_alerts = session.scalar(select(func.count(func.distinct(Alert.tenant_id))).where(
        Alert.tenant_id.isnot(None))) or 0
    return {
        "window_hours": hours,
        "total_alerts": total_alerts,
        "firing_alerts": firing,
        "total_incidents": total_incidents,
        "major_incidents": major_incidents,
        "active_incidents": active_incidents,
        "tenants_with_alerts": tenants_with_alerts,
        "is_platform_aggregate": True,
    }


def alert_volume_timeseries(session: Session, tenant_id, *, hours: int = 24, bucket: str = "1h") -> list[dict]:
    since = _now() - timedelta(hours=hours)
    rows = session.execute(select(func.strftime("%Y-%m-%dT%H:00:00", Alert.first_observed),
                                  func.count(Alert.id)).where(
        Alert.first_observed >= since,
        Alert.tenant_id == tenant_id).group_by(func.strftime("%Y-%m-%dT%H:00:00", Alert.first_observed))).all()
    return [{"bucket": r[0], "count": r[1]} for r in rows]


def incident_report(session: Session, tenant_id, *, days: int = 7) -> dict:
    since = _now() - timedelta(days=days)
    by_severity = dict(session.execute(select(Incident.severity, func.count(Incident.id)).where(
        Incident.tenant_id == tenant_id, Incident.detected_at >= since).group_by(Incident.severity)).all())
    return {
        "tenant_id": str(tenant_id) if tenant_id else None,
        "days": days,
        "by_severity": by_severity,
        "total": sum(by_severity.values()),
    }


def slo_budget_report(session: Session, tenant_id, *, limit: int = 100) -> list[dict]:
    rows = list(session.scalars(select(SloWindowState).where(
        SloWindowState.tenant_id == tenant_id).order_by(SloWindowState.computed_at.desc()).limit(limit)))
    return [{"slo_id": str(r.slo_id), "status": r.status, "remaining_budget": r.remaining_budget,
             "burn_rate": r.burn_rate, "fast_burn": r.fast_burn, "slow_burn": r.slow_burn,
             "window_start": r.window_start.isoformat(), "window_end": r.window_end.isoformat()} for r in rows]
