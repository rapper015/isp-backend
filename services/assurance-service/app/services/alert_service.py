"""Alert ingestion, normalization, dedup/grouping, inhibition, silencing, routing."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.alerts import (GROUPING_LABELS, SEVERITY_ORDER, dependency_suppression, fingerprint,
                             grouping_key, is_flapping, should_dedupe, validate_severity)
from ..domain.exceptions import NotFoundError
from ..domain.identity import assert_safe_labels, normalize_alert_name
from ..events import outbox
from ..models import (Alert, AlertEvent, AlertRoute, AlertSilence, NotificationDelivery)
from ..state_machine import guarded as alert_guarded

ALERT_STATE_FLOW = {
    "PENDING": {"FIRING", "SUPPRESSED", "SILENCED", "RESOLVED", "EXPIRED"},
    "FIRING": {"ACKNOWLEDGED", "SUPPRESSED", "SILENCED", "RESOLVED", "EXPIRED", "PENDING"},
    "ACKNOWLEDGED": {"SUPPRESSED", "SILENCED", "RESOLVED", "EXPIRED", "FIRING"},
    "SUPPRESSED": {"FIRING", "SILENCED", "RESOLVED", "EXPIRED"},
    "SILENCED": {"FIRING", "RESOLVED", "EXPIRED"},
    "RESOLVED": set(),
    "EXPIRED": set(),
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _move(session: Session, alert: Alert, target: str, *, actor: str | None = None, detail: dict | None = None):
    if target not in ALERT_STATE_FLOW.get(alert.state, set()):
        raise ValueError(f"invalid alert transition {alert.state} -> {target}")
    from_state = alert.state
    alert.state = target
    if target == "ACKNOWLEDGED":
        alert.acknowledged_by = actor
        alert.acknowledged_at = _now()
    if target == "RESOLVED":
        alert.resolved_at = _now()
    event = AlertEvent(tenant_id=alert.tenant_id, alert_id=alert.id,
                       transition=f"{from_state}->{target}", detail=detail or {}, observed_at=_now())
    session.add(event)
    return alert


def normalize_and_ingest(session: Session, *, service: str, alert_name: str, tenant_id,
                         severity: str = "MEDIUM", component: str | None = None,
                         resource: str | None = None, labels: dict | None = None,
                         impact: str | None = None, source: str = "collector",
                         correlation_id: str | None = None, observed_at: datetime | None = None) -> Alert:
    labels = labels or {}
    assert_safe_labels(labels)
    severity = validate_severity(severity)
    alert_name = normalize_alert_name(alert_name)
    fp = fingerprint(service, alert_name, resource, component, tenant_id)
    now = observed_at or _now()
    existing = session.scalars(select(Alert).where(Alert.fingerprint == fp)).first()
    if existing is not None:
        # Update dedup/grouping on the canonical alert.
        is_firing = existing.state in ("FIRING", "ACKNOWLEDGED", "SUPPRESSED", "SILENCED")
        if should_dedupe(is_firing, existing.last_observed or now, now,
                         existing.dedup_window_seconds):
            existing.firing_count += 1
        existing.last_observed = now
        existing.impact = impact or existing.impact
        existing.labels = labels
        existing.severity = severity
        if existing.state == "PENDING":
            _move(session, existing, "FIRING", detail={"reason": "firing threshold reached"})
        session.flush()
        _route(session, existing, correlation_id=correlation_id)
        return existing
    alert = Alert(tenant_id=tenant_id, fingerprint=fp, service=service, alert_name=alert_name,
                  component=component, resource=resource, severity=severity, impact=impact,
                  state="PENDING", labels=labels, first_observed=now, last_observed=now,
                  firing_count=1, source=source, evidence=[],
                  dedup_window_seconds=300)
    session.add(alert)
    session.flush()
    # Silence / suppression evaluation happens on state transition to FIRING.
    _move(session, alert, "FIRING", detail={"reason": "first observation"})
    session.flush()
    _route(session, alert, correlation_id=correlation_id)
    return alert


def acknowledge(session: Session, alert_id: uuid.UUID, actor: str) -> Alert:
    alert = _get_alert(session, alert_id)
    if alert.state not in ("FIRING", "SUPPRESSED", "SILENCED"):
        raise ValueError(f"cannot acknowledge alert in state {alert.state}")
    _move(session, alert, "ACKNOWLEDGED", actor=actor, detail={"actor": actor})
    return alert


def resolve(session: Session, alert_id: uuid.UUID, *, actor: str | None = None, detail: dict | None = None) -> Alert:
    alert = _get_alert(session, alert_id)
    _move(session, alert, "RESOLVED", actor=actor, detail=detail or {})
    outbox(session, "assurance.alert_resolved.v1", alert.tenant_id, None,
           {"alert_id": str(alert.id), "fingerprint": alert.fingerprint,
            "service": alert.service, "alert_name": alert.alert_name},
           idempotency_key=f"alert-resolved:{alert.id}")
    return alert


def expire(session: Session, alert_id: uuid.UUID) -> Alert:
    alert = _get_alert(session, alert_id)
    _move(session, alert, "EXPIRED")
    return alert


def _get_alert(session: Session, alert_id: uuid.UUID) -> Alert:
    alert = session.scalars(select(Alert).where(Alert.id == alert_id)).first()
    if alert is None:
        raise NotFoundError("alert not found")
    return alert


def list_alerts(session: Session, tenant_id, *, state: str | None = None, service: str | None = None,
                limit: int = 100):
    q = select(Alert).where(Alert.tenant_id == tenant_id)
    if state:
        q = q.where(Alert.state == state)
    if service:
        q = q.where(Alert.service == service)
    return list(session.scalars(q.order_by(Alert.first_observed.desc()).limit(limit)))


def _matching_silences(session: Session, alert: Alert, now: datetime) -> list[AlertSilence]:
    return list(session.scalars(select(AlertSilence).where(
        AlertSilence.tenant_id == alert.tenant_id,
        AlertSilence.state == "ACTIVE",
        AlertSilence.starts_at <= now,
        AlertSilence.ends_at >= now)))


def apply_silence(session: Session, alert: Alert, now: datetime | None = None) -> bool:
    now = now or _now()
    for silence in _matching_silences(session, alert, now):
        match = silence.match_labels or {}
        if all(alert.labels.get(k) == v for k, v in match.items()):
            if alert.state in ("FIRING", "PENDING"):
                _move(session, alert, "SILENCED", detail={"silence_id": str(silence.id)})
            return True
    return False


def dependency_check(session: Session, alert: Alert, now: datetime | None = None) -> bool:
    """Inhibit downstream alerts when a parent dependency is still firing."""
    now = now or _now()
    parents = _parent_alerts(session, alert)
    if dependency_suppression(alert, parents):
        if alert.state in ("FIRING", "PENDING", "ACKNOWLEDGED"):
            _move(session, alert, "SUPPRESSED", detail={"reason": "dependency suppression"})
        return True
    if alert.state == "SUPPRESSED":
        _move(session, alert, "FIRING", detail={"reason": "dependency resolved"})
    return False


def _parent_alerts(session: Session, alert: Alert) -> list[Alert]:
    """Inhibition parents: any higher-severity alert on the same component
    (shared infrastructure) that is still firing/acknowledged."""
    if not alert.component:
        return []
    return list(session.scalars(select(Alert).where(
        Alert.tenant_id == alert.tenant_id,
        Alert.component == alert.component,
        Alert.state.in_(("FIRING", "ACKNOWLEDGED")),
        Alert.id != alert.id)))


def _route(session: Session, alert: Alert, correlation_id: str | None = None) -> None:
    if alert.state not in ("FIRING", "ACKNOWLEDGED"):
        return
    routes = list(session.scalars(select(AlertRoute).where(AlertRoute.is_active.is_(True))))
    for route in sorted(routes, key=lambda r: len(r.match_labels or {}), reverse=True):
        match = route.match_labels or {}
        if all(alert.labels.get(k) == v for k, v in match.items()):
            delivery = NotificationDelivery(tenant_id=alert.tenant_id, alert_id=alert.id,
                                            route=route.name, channel=route.channel,
                                            recipient=route.recipients[0] if route.recipients else None,
                                            status="DELIVERED", provider_ref=str(uuid.uuid4()),
                                            detail={"escalation_policy": route.escalation_policy},
                                            sent_at=_now())
            session.add(delivery)
            session.flush()
            return
    # Fallback route
    delivery = NotificationDelivery(tenant_id=alert.tenant_id, alert_id=alert.id, route="DEFAULT",
                                    channel="STATUS_PAGE", status="DELIVERED",
                                    provider_ref=str(uuid.uuid4()), detail={}, sent_at=_now())
    session.add(delivery)
    session.flush()


def evaluate_flapping(session: Session, alert: Alert, history: list[str]) -> bool:
    if is_flapping(history):
        _move(session, alert, "SUPPRESSED", detail={"reason": "flapping detected"})
        return True
    return False
