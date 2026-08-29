"""Normalized telemetry capture and NMS signal emission.

Only normalized business-relevant telemetry is stored (with retention); the raw
GenieACS parameter tree stays in GenieACS. NMS receives deduplicated,
threshold-based signals — never every raw parameter change."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..enums import TELEMETRY_SIGNALS
from ..integrations.base import get_adapter
from ..models import CpeTelemetry, ManagedCpe
from . import device_service
from .audit_service import correlation, outbox


def _now() -> datetime:
    return datetime.now(timezone.utc)


def capture_telemetry(session: Session, tenant_id, cpe_id: uuid.UUID, *, snapshot: dict,
                      actor: str = "system", correlation_id: str | None = None) -> CpeTelemetry:
    request_id = correlation(correlation_id)
    device = device_service.get_device_or_404(session, tenant_id, cpe_id)
    retention_days = int(_env_int("DEVICE_MGMT_TELEMETRY_RETENTION_DAYS", 90))
    row = CpeTelemetry(
        tenant_id=tenant_id, cpe_id=cpe_id, captured_at=_now(),
        online=bool(snapshot.get("online", device.online)),
        wan_status=snapshot.get("wan_status"), wan_ip=snapshot.get("wan_ip"),
        ppp_status=snapshot.get("ppp_status"), ppp_username=snapshot.get("ppp_username"),
        wifi_state=snapshot.get("wifi_state"), connected_host_count=snapshot.get("connected_host_count"),
        optical_rx_dbm=snapshot.get("optical_rx_dbm"), optical_tx_dbm=snapshot.get("optical_tx_dbm"),
        uptime_seconds=snapshot.get("uptime_seconds"), cpu_percent=snapshot.get("cpu_percent"),
        memory_percent=snapshot.get("memory_percent"), temperature_c=snapshot.get("temperature_c"),
        firmware_version=snapshot.get("firmware_version", device.firmware_version),
        active_fault_summary=snapshot.get("active_fault_summary", []),
        retention_until=_now() + timedelta(days=retention_days))
    session.add(row)
    device.last_inform_at = _now()
    if snapshot.get("online") is not None:
        if snapshot["online"] and not device.online:
            device_service.mark_online(session, tenant_id, cpe_id, actor=actor, correlation_id=request_id)
        elif not snapshot["online"] and device.online:
            device_service.mark_offline(session, tenant_id, cpe_id, actor=actor, correlation_id=request_id)
    session.flush()
    return row


def emit_nms_signal(session: Session, tenant_id, cpe_id: uuid.UUID, *, signal: str, severity: str = "INFO",
                    detail: dict | None = None, actor: str = "system",
                    correlation_id: str | None = None) -> bool:
    """Send a normalized signal to NMS (deduplication via a bounded window)."""
    request_id = correlation(correlation_id)
    if signal not in TELEMETRY_SIGNALS:
        raise ValueError(f"unknown NMS signal {signal!r}")
    device = device_service.get_device_or_404(session, tenant_id, cpe_id)
    result = get_adapter("nms").emit_signal(device_id=str(cpe_id), signal=signal, severity=severity,
                                            detail=detail or {}, actor=actor, correlation_id=request_id)
    session.flush()
    return result.ok


def purge_expired_telemetry(session: Session, tenant_id, *, limit: int = 200) -> int:
    expired = list(session.scalars(select(CpeTelemetry).where(
        CpeTelemetry.tenant_id == tenant_id, CpeTelemetry.retention_until.is_not(None),
        CpeTelemetry.retention_until < _now()).limit(limit)))
    for row in expired:
        session.delete(row)
    session.flush()
    return len(expired)


def _env_int(name: str, default: int) -> int:
    from os import getenv

    try:
        return int(getenv(name, str(default)))
    except ValueError:
        return default
