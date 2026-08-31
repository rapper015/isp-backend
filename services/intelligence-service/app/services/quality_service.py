"""Data-quality measurement + dataset snapshots."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..domain.exceptions import NotFoundError
from ..models import AnalyticalRecord, DataQualityCheck, DatasetSnapshot, RawEvent
from .ingestion_service import get_contract


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def measure_quality(session: Session, tenant_id, contract: str) -> dict:
    """Run completeness/freshness/uniqueness/schema checks for a contract."""
    checks = []
    total = session.scalar(select(func.count(RawEvent.id)).where(RawEvent.contract == contract)) or 0
    quarantined = session.scalar(select(func.count(RawEvent.id)).where(
        RawEvent.contract == contract, RawEvent.state == "QUARANTINED")) or 0
    # Completeness: quarantined ratio within a small tolerance.
    completeness = "PASS" if (total == 0 or quarantined / total < 0.05) else "WARN"
    checks.append(_record(session, tenant_id, contract, "completeness", completeness,
                          {"total": total, "quarantined": quarantined}))
    # Uniqueness: raw event ids unique by construction; check analytical dupes.
    dupes = session.scalar(select(func.count(RawEvent.id)).where(
        RawEvent.contract == contract).group_by(RawEvent.event_id).having(func.count() > 1)) or 0
    uniqueness = "PASS" if dupes == 0 else "WARN"
    checks.append(_record(session, tenant_id, contract, "uniqueness", uniqueness, {"duplicates": dupes}))
    # Freshness: latest event time within 2x retention-ish / 7 days.
    latest = session.scalar(select(func.max(RawEvent.event_time)).where(RawEvent.contract == contract))
    latest = _utc(latest)
    freshness = "PASS"
    if latest is not None and (_now() - latest) > timedelta(days=7):
        freshness = "FAIL"
    checks.append(_record(session, tenant_id, contract, "freshness", freshness,
                          {"latest_event_time": latest.isoformat() if latest else None}))
    # Validity/schema: required fields populated on analytical records.
    invalid = session.scalar(select(func.count(AnalyticalRecord.id)).where(
        AnalyticalRecord.contract == contract, AnalyticalRecord.normalized == {})) or 0
    validity = "PASS" if invalid == 0 else "WARN"
    checks.append(_record(session, tenant_id, contract, "schema", validity, {"invalid": invalid}))
    return {"contract": contract, "checks": checks,
            "overall": "FAIL" if any(c.result == "FAIL" for c in checks)
            else ("WARN" if any(c.result == "WARN" for c in checks) else "PASS")}


def _record(session: Session, tenant_id, contract: str, check_type: str, result: str,
            detail: dict) -> DataQualityCheck:
    row = DataQualityCheck(tenant_id=tenant_id, contract=contract, check_type=check_type,
                           result=result, detail=detail, measured_at=_now())
    session.add(row)
    session.flush()
    return row


def snapshot_dataset(session: Session, *, tenant_id, code: str, name: str | None = None,
                     contracts: list[str] | None = None, criteria: dict | None = None,
                     created_by: str | None = None) -> DatasetSnapshot:
    contracts = contracts or []
    rows = session.scalars(select(AnalyticalRecord).where(
        AnalyticalRecord.contract.in_(contracts) if contracts else True)).all()
    row_count = len(rows)
    sample = [r.normalized for r in rows[:200]]
    checksum = hashlib.sha256(json.dumps(sample, sort_keys=True, default=str).encode()).hexdigest()
    snap = DatasetSnapshot(tenant_id=tenant_id, code=code, name=name or code,
                           contract_filter=contracts, criteria=criteria or {},
                           row_count=row_count, checksum=checksum,
                           state="DRAFT", created_by=created_by)
    session.add(snap)
    session.flush()
    return snap


def approve_dataset(session: Session, snapshot_id: uuid.UUID, approved_by: str) -> DatasetSnapshot:
    snap = session.get(DatasetSnapshot, snapshot_id)
    if snap is None:
        raise NotFoundError("dataset snapshot not found")
    snap.state = "APPROVED"
    snap.approved_by = approved_by
    return snap


def snapshot_rows(session: Session, snapshot_id: uuid.UUID) -> list[AnalyticalRecord]:
    snap = session.get(DatasetSnapshot, snapshot_id)
    if snap is None:
        raise NotFoundError("dataset snapshot not found")
    contracts = snap.contract_filter or []
    q = select(AnalyticalRecord)
    if contracts:
        q = q.where(AnalyticalRecord.contract.in_(contracts))
    return list(session.scalars(q))
