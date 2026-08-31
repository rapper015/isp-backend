"""Tenant-aware, concurrency-safe human-readable ticket numbering.

Format: ``TKT-2026-00001234``.

The sequence is per (tenant, year) and is incremented atomically with an
INSERT .. ON CONFLICT .. DO UPDATE .. RETURNING statement that works on both
PostgreSQL and SQLite (>= 3.35). The ticket number is additionally enforced
unique per tenant, so a lost update can never produce a duplicate. Numbers are
immutable and never reused after a ticket is cancelled or deleted. UUIDs remain
the internal identity; the number is only the external handle.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session


def _now() -> datetime:
    return datetime.now(timezone.utc)


def next_ticket_number(session: Session, tenant_id, year: int | None = None, prefix: str = "TKT") -> str:
    """Allocate the next ticket number for the tenant/year atomically."""
    year = year or _now().year
    result = session.execute(
        text(
            """
            INSERT INTO support_ticket_number_sequences (tenant_id, year, last_number)
            VALUES (:tenant_id, :year, 1)
            ON CONFLICT (tenant_id, year)
            DO UPDATE SET last_number = support_ticket_number_sequences.last_number + 1
            RETURNING last_number
            """
        ),
        {"tenant_id": str(tenant_id), "year": int(year)},
    )
    sequence = result.scalar_one()
    return f"{prefix}-{year}-{int(sequence):08d}"


def ticket_number_format(number: str) -> bool:
    """Validate a ticket-number-shaped string (used by search/normalization)."""
    parts = number.split("-")
    if len(parts) != 3 or parts[0] != "TKT":
        return False
    try:
        int(parts[1])
        int(parts[2])
    except ValueError:
        return False
    return True
