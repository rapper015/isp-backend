"""Synthetic checks and results."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.exceptions import NotFoundError
from ..enums import SYNTHETIC_KINDS
from ..models import SyntheticCheck, SyntheticResult


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_check(session: Session, *, tenant_id, code: str, kind: str, target: str | None = None,
                 frequency_seconds: int = 300, timeout_seconds: int = 10, tags: list | None = None,
                 is_active: bool = True) -> SyntheticCheck:
    if kind not in SYNTHETIC_KINDS:
        raise ValueError(f"unknown synthetic kind {kind!r}")
    row = SyntheticCheck(tenant_id=tenant_id, code=code, kind=kind, target=target,
                         frequency_seconds=frequency_seconds, timeout_seconds=timeout_seconds,
                         tags=tags or [], is_active=is_active)
    session.add(row)
    session.flush()
    return row


def record_result(session: Session, *, tenant_id, check_code: str, result: str, latency_ms: float = 0.0,
                  detail: dict | None = None, checked_at: datetime | None = None) -> SyntheticResult:
    check = session.scalars(select(SyntheticCheck).where(SyntheticCheck.code == check_code,
                                                         SyntheticCheck.tenant_id == tenant_id)).first()
    if check is None:
        raise NotFoundError(f"synthetic check {check_code!r} not found")
    if result not in ("PASS", "FAIL", "TIMEOUT", "ERROR"):
        raise ValueError(f"invalid synthetic result {result!r}")
    row = SyntheticResult(tenant_id=tenant_id, check_id=check.id, result=result, latency_ms=latency_ms,
                          detail=detail or {}, checked_at=checked_at or _now())
    session.add(row)
    session.flush()
    return row


def list_checks(session: Session, tenant_id, *, limit: int = 100):
    return list(session.scalars(select(SyntheticCheck).where(
        SyntheticCheck.tenant_id == tenant_id).limit(limit)))
