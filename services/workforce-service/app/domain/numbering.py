"""Tenant-aware, concurrency-safe human-readable work-order numbering.

Format: ``WO-2026-00001234``. The sequence is per (tenant, year) and is
incremented atomically with INSERT .. ON CONFLICT .. DO UPDATE .. RETURNING
(works on PostgreSQL and SQLite >= 3.35). Numbers are unique, immutable, never
reused, and safe under concurrent creation."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session


def _now() -> datetime:
    return datetime.now(timezone.utc)


def next_work_order_number(session: Session, tenant_id, year: int | None = None, prefix: str = "WO") -> str:
    year = year or _now().year
    result = session.execute(
        text(
            """
            INSERT INTO workforce_work_order_number_sequences (tenant_id, year, last_number)
            VALUES (:tenant_id, :year, 1)
            ON CONFLICT (tenant_id, year)
            DO UPDATE SET last_number = workforce_work_order_number_sequences.last_number + 1
            RETURNING last_number
            """
        ),
        {"tenant_id": str(tenant_id), "year": int(year)},
    )
    sequence = result.scalar_one()
    return f"{prefix}-{year}-{int(sequence):08d}"
