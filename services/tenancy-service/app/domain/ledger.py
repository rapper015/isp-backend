"""Immutable ledger (M4 pattern reused for settlements/wallets).

Entries are never edited or deleted; corrections create REVERSAL entries.
Balances are derived projections rebuilt from journal lines."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import JournalEntry, JournalLine, LedgerBalanceProjection
from .exceptions import LedgerError

JOURNAL_ENTRY_TYPES = (
    "COMMISSION", "SETTLEMENT", "SETTLEMENT_PAYOUT", "WALLET", "ADJUSTMENT",
    "CLAWBACK", "REVERSAL", "OPENING_BALANCE",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_account(session: Session, tenant_id, *, code: str, name: str, kind: str = "LIABILITY"):
    from ..models import LedgerAccount

    account = session.scalars(select(LedgerAccount).where(
        LedgerAccount.tenant_id == tenant_id, LedgerAccount.code == code)).first()
    if account is None:
        account = LedgerAccount(tenant_id=tenant_id, code=code, name=name, kind=kind)
        session.add(account)
        session.flush()
    return account


def next_entry_number(session: Session, tenant_id: uuid.UUID) -> str:
    count = session.scalar(select(func.count()).select_from(JournalEntry).where(
        JournalEntry.tenant_id == tenant_id)) or 0
    return f"TEN{str(tenant_id)[:8].upper()}-{count + 1:06d}"


def post_entry(session: Session, tenant_id, *, entry_type: str, lines: list[dict],
               reference: str | None = None, posted_by: str = "system",
               correlation_id: str | None = None, entry_number: str | None = None) -> JournalEntry:
    """Post an immutable journal entry. Lines are [{'account': code, 'debit': x, 'credit': y}]."""
    if entry_type not in JOURNAL_ENTRY_TYPES:
        raise LedgerError(f"unsupported journal entry type {entry_type!r}")
    if len(lines) < 2:
        raise LedgerError("journal entry requires at least two lines")
    debit = sum(float(line.get("debit", 0)) for line in lines)
    credit = sum(float(line.get("credit", 0)) for line in lines)
    if round(debit, 2) != round(credit, 2):
        raise LedgerError(f"journal entry is not balanced (debit {debit} != credit {credit})")
    entry = JournalEntry(
        tenant_id=tenant_id, entry_type=entry_type,
        entry_number=entry_number or next_entry_number(session, tenant_id),
        reference=reference, posted_at=_now(), posted_by=posted_by, correlation_id=correlation_id)
    session.add(entry)
    session.flush()
    for line in lines:
        session.add(JournalLine(tenant_id=tenant_id, entry_id=entry.id,
                                account_code=line["account"],
                                debit=float(line.get("debit", 0)),
                                credit=float(line.get("credit", 0))))
    session.flush()
    rebuild_projection(session, tenant_id)
    return entry


def reverse_entry(session: Session, tenant_id, entry_id: uuid.UUID, *, reason: str,
                  posted_by: str = "system", correlation_id: str | None = None) -> JournalEntry:
    """Create a full REVERSAL entry; the original is never edited."""
    original = session.get(JournalEntry, entry_id)
    if original is None or original.tenant_id != tenant_id:
        raise LedgerError("journal entry not found")
    if original.entry_type == "REVERSAL":
        raise LedgerError("cannot reverse a reversal")
    lines = [{"account": l.account_code, "debit": float(l.credit), "credit": float(l.debit)}
             for l in session.scalars(select(JournalLine).where(JournalLine.entry_id == original.id))]
    reversal = post_entry(session, tenant_id, entry_type="REVERSAL", lines=lines,
                          reference=reason, posted_by=posted_by, correlation_id=correlation_id)
    reversal.reversal_of = original.id
    session.flush()
    return reversal


def rebuild_projection(session: Session, tenant_id) -> None:
    """Derive balances from journal lines (projection is rebuildable, not source)."""
    rows = session.execute(
        select(JournalLine.account_code,
               func.coalesce(func.sum(JournalLine.debit), 0) - func.coalesce(func.sum(JournalLine.credit), 0))
        .where(JournalLine.tenant_id == tenant_id)
        .group_by(JournalLine.account_code)).all()
    for code, balance in rows:
        projection = session.scalars(select(LedgerBalanceProjection).where(
            LedgerBalanceProjection.tenant_id == tenant_id,
            LedgerBalanceProjection.account_code == code)).first()
        if projection is None:
            projection = LedgerBalanceProjection(tenant_id=tenant_id, account_code=code, balance=0.0)
            session.add(projection)
        projection.balance = float(balance)
    session.flush()
