"""Idempotent consumers mapping domain events into assurance records.

Domain events never carry raw telemetry into Django storage; they are
translated into SLI/KPI measurements, change events and network observations
that are already aggregated at the edges.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..events import canonical_event_type, consume_once
from ..models import ChangeEvent, NetworkObservation
from ..services import kpi_service, slo_service

logger = logging.getLogger("assurance.consumers")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _tenant(value) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


def _measurement_map(event_type: str):
    """Map consumed events to SLI/KPI measurement deltas."""
    mapping = {
        "oss.order.created.v1": ("sli_provisioning_success", "kpi_provisioning_jobs", None, 0),
        "oss.order.activated.v1": ("sli_provisioning_success", "kpi_provisioning_success_rate", 1, 1),
        "billing.payment.captured.v1": ("sli_payment_success", "kpi_payment_success_rate", 1, 1),
        "billing.payment.failed.v1": ("sli_payment_success", "kpi_payment_success_rate", 0, 1),
        "crm.customer.created.v1": (None, "kpi_login_attempts", None, 0),
        "aaa.session.stale.v1": (None, "kpi_active_radius_sessions", None, 0),
    }
    return mapping.get(event_type)


def handle(session: Session, envelope: dict) -> None:
    event_type = envelope.get("event_type", "")
    event_id = envelope.get("event_id")
    if not event_id:
        logger.warning("dropping event without id: %s", event_type)
        return
    try:
        canonical = canonical_event_type(event_type)
    except ValueError:
        logger.info("ignoring unconsumed event type %s", event_type)
        return
    consumer = f"assurance:{canonical}"
    if not consume_once(session, str(event_id), consumer):
        logger.info("duplicate event %s (already consumed)", event_id)
        return
    tenant_id = _tenant(envelope.get("tenant_id"))
    correlation_id = envelope.get("correlation_id")
    payload = envelope.get("payload") or {}
    recorded_at = _now()
    # Change events for correlation of incidents with deployments.
    category = {
        "firmware.rollout.started.v1": "CHANGE", "network.policy.changed.v1": "CHANGE",
        "network.policy.deployed.v1": "CHANGE", "configuration.profile.changed.v1": "CHANGE",
        "workforce.job.completed.v1": "CHANGE",
    }.get(canonical)
    if category == "CHANGE":
        session.add(ChangeEvent(tenant_id=tenant_id, change_type="DEPLOYMENT",
                                entity_type=canonical,
                                entity_ref=payload.get("job_id") or payload.get("deployment_id"),
                                occurred_at=recorded_at, detail=payload,
                                correlation_id=correlation_id))
    # Network observations
    if canonical in ("nas.health_changed.v1", "device.cpe.offline.v1", "device.cpe.online.v1"):
        check_type = canonical.split(".")[0]
        session.add(NetworkObservation(
            tenant_id=tenant_id,
            device_ref=str(payload.get("nas_id") or payload.get("cpe_id") or payload.get("device_id") or "unknown"),
            check_type=check_type,
            status="DEGRADED" if "offline" in canonical or "health_changed" in canonical else "OK",
            metrics=payload, observed_at=recorded_at, source=canonical))
    mapping = _measurement_map(canonical)
    if mapping:
        sli_code, kpi_code, good, total = mapping
        if sli_code and total is not None:
            try:
                slo_service.record_measurement(session, tenant_id, sli_code, good=float(good),
                                               total=float(total), source_ref=event_id)
            except Exception:  # noqa: BLE001 - SLI may not exist yet
                logger.warning("SLI %s not recorded for %s", sli_code, canonical)
        if kpi_code and total is not None:
            try:
                kpi_service.record_measurement(session, tenant_id, kpi_code,
                                               period_key=recorded_at.strftime("%Y-%m-%d"),
                                               value=float(good), dimensions={"source": canonical})
            except Exception:  # noqa: BLE001
                logger.warning("KPI %s not recorded for %s", kpi_code, canonical)
    session.flush()
