"""Maintenance windows and exceptions."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.exceptions import NotFoundError
from ..events import outbox
from ..models import MaintenanceException, MaintenanceWindow, SloDefinition

MAINTENANCE_FLOW = {
    "REQUESTED": {"APPROVED", "REJECTED", "CANCELLED"},
    "APPROVED": {"ACTIVE", "CANCELLED", "COMPLETED"},
    "REJECTED": set(),
    "CANCELLED": set(),
    "ACTIVE": {"COMPLETED", "CANCELLED"},
    "COMPLETED": set(),
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_window(session: Session, *, tenant_id, service_id, starts_at: datetime, ends_at: datetime,
                  maintenance_type: str = "PLANNED", reason: str | None = None, owner: str | None = None,
                  scope_kind: str = "SERVICE", scope_ref: str | None = None,
                  sla_treatment: str = "EXCLUDE", alert_suppression: bool = True,
                  correlation_id: str | None = None) -> MaintenanceWindow:
    row = MaintenanceWindow(tenant_id=tenant_id, service_id=service_id, starts_at=starts_at,
                            ends_at=ends_at, maintenance_type=maintenance_type, reason=reason,
                            owner=owner, scope_kind=scope_kind, scope_ref=scope_ref,
                            sla_treatment=sla_treatment, alert_suppression=alert_suppression,
                            state="REQUESTED", correlation_id=correlation_id)
    session.add(row)
    session.flush()
    return row


def approve(session: Session, window_id: uuid.UUID, *, approved_by: str) -> MaintenanceWindow:
    w = _get(session, window_id)
    if w.state not in ("REQUESTED",):
        raise ValueError(f"cannot approve maintenance in state {w.state}")
    w.state = "APPROVED"
    w.approved_by = approved_by
    session.flush()
    outbox(session, "assurance.maintenance_window_approved.v1", w.tenant_id, w.correlation_id,
           {"maintenance_id": str(w.id), "service_id": str(w.service_id) if w.service_id else None},
           idempotency_key=f"maintenance-approved:{w.id}")
    return w


def reject(session: Session, window_id: uuid.UUID) -> MaintenanceWindow:
    w = _get(session, window_id)
    if w.state != "REQUESTED":
        raise ValueError(f"cannot reject maintenance in state {w.state}")
    w.state = "REJECTED"
    return w


def activate(session: Session, window_id: uuid.UUID) -> MaintenanceWindow:
    w = _get(session, window_id)
    if w.state != "APPROVED":
        raise ValueError(f"cannot activate maintenance in state {w.state}")
    w.state = "ACTIVE"
    return w


def complete(session: Session, window_id: uuid.UUID) -> MaintenanceWindow:
    w = _get(session, window_id)
    if w.state not in ("ACTIVE", "APPROVED"):
        raise ValueError(f"cannot complete maintenance in state {w.state}")
    w.state = "COMPLETED"
    return w


def cancel(session: Session, window_id: uuid.UUID) -> MaintenanceWindow:
    w = _get(session, window_id)
    if w.state in ("COMPLETED", "REJECTED"):
        raise ValueError(f"cannot cancel maintenance in state {w.state}")
    w.state = "CANCELLED"
    return w


def add_exception(session: Session, window_id: uuid.UUID, slo_id: uuid.UUID, *,
                  approved_by: str | None = None, reason: str | None = None) -> MaintenanceException:
    w = _get(session, window_id)
    existing = session.scalars(select(MaintenanceException).where(
        MaintenanceException.maintenance_id == w.id,
        MaintenanceException.slo_id == slo_id)).first()
    if existing is not None:
        return existing
    row = MaintenanceException(tenant_id=w.tenant_id, maintenance_id=w.id, slo_id=slo_id,
                               approved_by=approved_by, reason=reason)
    session.add(row)
    session.flush()
    return row


def active_for_slo(session: Session, slo_id: uuid.UUID, now: datetime | None = None) -> list[MaintenanceWindow]:
    now = now or _now()
    return list(session.scalars(select(MaintenanceWindow).join(
        MaintenanceException, MaintenanceException.maintenance_id == MaintenanceWindow.id).where(
        MaintenanceWindow.starts_at <= now, MaintenanceWindow.ends_at >= now,
        MaintenanceWindow.state.in_(("ACTIVE", "APPROVED")),
        MaintenanceException.slo_id == slo_id)))


def _get(session: Session, window_id: uuid.UUID) -> MaintenanceWindow:
    w = session.scalars(select(MaintenanceWindow).where(MaintenanceWindow.id == window_id)).first()
    if w is None:
        raise NotFoundError("maintenance window not found")
    return w
