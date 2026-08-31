"""KPI definition, measurement and targeting."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.exceptions import NotFoundError
from ..models import KpiDefinition, KpiMeasurement, KpiTarget


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_kpi(session: Session, data: dict) -> KpiDefinition:
    kpi = KpiDefinition(**{k: v for k, v in data.items() if k in {
        "code", "name", "business_meaning", "owner", "formula", "numerator", "denominator",
        "data_sources", "dimensions", "unit", "freshness_seconds", "validation_status"}})
    session.add(kpi)
    session.flush()
    return kpi


def record_measurement(session: Session, tenant_id, kpi_code: str, *, period_key: str, value: float,
                       quality: str = "FRESH", dimensions: dict | None = None,
                       measured_at: datetime | None = None) -> KpiMeasurement:
    kpi = session.scalars(select(KpiDefinition).where(KpiDefinition.code == kpi_code)).first()
    if kpi is None:
        raise NotFoundError(f"KPI {kpi_code!r} not found")
    row = KpiMeasurement(tenant_id=tenant_id, kpi_id=kpi.id, period_key=period_key, value=value,
                         quality=quality, dimensions=dimensions or {}, measured_at=measured_at or _now())
    session.add(row)
    session.flush()
    return row


def set_target(session: Session, tenant_id, kpi_code: str, *, target: float, direction: str = "ABOVE",
               target_key: str = "DEFAULT") -> KpiTarget:
    kpi = session.scalars(select(KpiDefinition).where(KpiDefinition.code == kpi_code)).first()
    if kpi is None:
        raise NotFoundError(f"KPI {kpi_code!r} not found")
    row = KpiTarget(tenant_id=tenant_id, kpi_id=kpi.id, target_key=target_key, target=target,
                    direction=direction)
    session.add(row)
    session.flush()
    return row


def latest_measurement(session: Session, tenant_id, kpi_code: str) -> dict | None:
    kpi = session.scalars(select(KpiDefinition).where(KpiDefinition.code == kpi_code)).first()
    if kpi is None:
        return None
    row = session.execute(select(KpiMeasurement).where(
        KpiMeasurement.kpi_id == kpi.id,
        KpiMeasurement.tenant_id == tenant_id).order_by(KpiMeasurement.measured_at.desc()).limit(1)
    ).scalars().first()
    if row is None:
        return None
    return {"code": kpi.code, "period_key": row.period_key, "value": row.value,
            "quality": row.quality, "dimensions": row.dimensions, "measured_at": row.measured_at.isoformat()}


def list_kpis(session: Session, tenant_id, *, limit: int = 100):
    kpis = list(session.scalars(select(KpiDefinition).limit(limit)))
    out = []
    for kpi in kpis:
        entry = {"code": kpi.code, "name": kpi.name, "unit": kpi.unit, "owner": kpi.owner,
                 "validation_status": kpi.validation_status}
        latest = session.execute(select(KpiMeasurement).where(
            KpiMeasurement.kpi_id == kpi.id).order_by(KpiMeasurement.measured_at.desc()).limit(1)
        ).scalars().first()
        if latest is not None and latest.tenant_id == tenant_id:
            entry["latest"] = {"period_key": latest.period_key, "value": latest.value}
        out.append(entry)
    return out
