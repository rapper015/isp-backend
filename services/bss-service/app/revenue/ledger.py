"""Immutable double-entry subledger.

Posted journal entries are never edited or deleted; corrections use reversals.
Balances are derived from journal lines and validated projections."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import SessionLocal
from .money import money
from .models import AccountingPeriod, JournalEntry, JournalLine, LedgerAccount, LedgerBalanceProjection


class LedgerError(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def period_key_for(date: datetime | None = None) -> str:
    return (date or _now()).strftime("%Y-%m")


def ensure_account(
    session: Session,
    tenant_id,
    *,
    code: str,
    name: str,
    kind: str,
    currency: str = "INR",
    normal_balance: str = "DEBIT",
) -> LedgerAccount:
    account = session.scalar(select(LedgerAccount).where(LedgerAccount.tenant_id == tenant_id, LedgerAccount.code == code))
    if account is None:
        account = LedgerAccount(tenant_id=tenant_id, code=code, name=name, kind=kind, currency=currency, normal_balance=normal_balance)
        session.add(account)
        session.flush()
    return account


def post_entry(
    session: Session,
    tenant_id,
    *,
    entry_type: str,
    currency: str,
    lines: list[tuple[str, str, Decimal]],
    effective_date: datetime | None = None,
    correlation_id: str,
    description: str,
    source_event: dict | None = None,
    actor: str | None = None,
    entry_number: str | None = None,
) -> JournalEntry:
    """Post a balanced double-entry journal entry.

    lines: list of (account_code, debit|credit, amount).
    Raises LedgerError if the entry is unbalanced or has < 2 lines."""
    if len(lines) < 2:
        raise LedgerError("journal entry must have at least two lines")
    total_debit = Decimal("0.00")
    total_credit = Decimal("0.00")
    resolved: list[tuple[LedgerAccount, Decimal, Decimal]] = []
    for account_code, direction, amount in lines:
        amount = money(amount)
        if amount <= 0:
            raise LedgerError("journal line amount must be positive")
        account = ensure_account(session, tenant_id, code=account_code, name=account_code, kind="ASSET", currency=currency)
        debit = amount if direction.upper() == "DEBIT" else Decimal("0.00")
        credit = amount if direction.upper() == "CREDIT" else Decimal("0.00")
        total_debit += debit
        total_credit += credit
        resolved.append((account, debit, credit))
    if total_debit != total_credit:
        raise LedgerError(f"journal entry unbalanced: debits {total_debit} != credits {total_credit}")
    effective = effective_date or _now()
    entry = JournalEntry(
        tenant_id=tenant_id,
        entry_number=entry_number or f"JE-{uuid.uuid4().hex[:12].upper()}",
        entry_type=entry_type,
        currency=currency,
        period_key=period_key_for(effective),
        effective_date=effective,
        correlation_id=correlation_id,
        description=description,
        source_event=source_event or {},
        actor=actor,
    )
    session.add(entry)
    session.flush()
    for account, debit, credit in resolved:
        session.add(JournalLine(tenant_id=tenant_id, entry_id=entry.id, account_id=account.id, debit=debit, credit=credit, currency=currency))
    session.flush()
    _upsert_projection(session, tenant_id, entry)
    return entry


def reverse_entry(session: Session, tenant_id, entry_number: str, *, actor: str, reason: str, correlation_id: str) -> JournalEntry:
    """Create a reversal of a posted entry. The original is never modified."""
    original = session.scalar(select(JournalEntry).where(JournalEntry.tenant_id == tenant_id, JournalEntry.entry_number == entry_number))
    if original is None:
        raise LedgerError(f"journal entry not found: {entry_number}")
    if original.reversal_of is not None:
        raise LedgerError(f"journal entry {entry_number} is itself a reversal")
    lines = list(session.scalars(select(JournalLine).where(JournalLine.entry_id == original.id)))
    reversed_lines = [(line.account_id, "CREDIT" if line.debit else "DEBIT", line.debit or line.credit) for line in lines]
    # Map account ids back to codes for post_entry.
    account_ids = [line.account_id for line in lines]
    accounts = {account.id: account for account in session.scalars(select(LedgerAccount).where(LedgerAccount.id.in_(account_ids)))}
    code_lines = [(accounts[account_id].code, direction, amount) for account_id, direction, amount in reversed_lines]
    reversal = post_entry(
        session,
        tenant_id,
        entry_type="REVERSAL",
        currency=original.currency,
        lines=code_lines,
        effective_date=_now(),
        correlation_id=correlation_id,
        description=f"reversal of {entry_number}: {reason}",
        source_event={"reverses": entry_number, "reason": reason},
        actor=actor,
    )
    reversal.reversal_of = original.id
    session.flush()
    return reversal


def _upsert_projection(session: Session, tenant_id, entry: JournalEntry) -> None:
    lines = list(session.scalars(select(JournalLine).where(JournalLine.entry_id == entry.id)))
    for line in lines:
        projection = session.scalar(
            select(LedgerBalanceProjection).where(
                LedgerBalanceProjection.tenant_id == tenant_id,
                LedgerBalanceProjection.account_id == line.account_id,
                LedgerBalanceProjection.period_key == entry.period_key,
            )
        )
        delta = (line.debit or 0) - (line.credit or 0)
        if projection is None:
            projection = LedgerBalanceProjection(tenant_id=tenant_id, account_id=line.account_id, period_key=entry.period_key, balance=Decimal("0.00"))
            session.add(projection)
            session.flush()
        projection.balance = money(Decimal(projection.balance or 0) + delta)


def rebuild_projection(session: Session, tenant_id, period_key: str) -> dict[str, str]:
    """Rebuild balance projections for a period from immutable journal lines."""
    rows = session.execute(
        select(JournalLine.account_id, func.coalesce(func.sum(JournalLine.debit), 0) - func.coalesce(func.sum(JournalLine.credit), 0))
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .where(JournalEntry.tenant_id == tenant_id, JournalEntry.period_key == period_key)
        .group_by(JournalLine.account_id)
    )
    balances = {str(account_id): money(balance) for account_id, balance in rows}
    for account_id, balance in balances.items():
        projection = session.scalar(
            select(LedgerBalanceProjection).where(
                LedgerBalanceProjection.tenant_id == tenant_id,
                LedgerBalanceProjection.account_id == uuid.UUID(account_id),
                LedgerBalanceProjection.period_key == period_key,
            )
        )
        if projection is None:
            projection = LedgerBalanceProjection(tenant_id=tenant_id, account_id=uuid.UUID(account_id), period_key=period_key, balance=Decimal("0.00"))
            session.add(projection)
        projection.balance = balance
    return {account_id: str(balance) for account_id, balance in balances.items()}


def account_balances(session: Session, tenant_id, period_key: str | None = None) -> dict[str, str]:
    stmt = select(LedgerBalanceProjection).where(LedgerBalanceProjection.tenant_id == tenant_id)
    if period_key:
        stmt = stmt.where(LedgerBalanceProjection.period_key == period_key)
    return {str(item.account_id): str(item.balance) for item in session.scalars(stmt)}


def ensure_period(session: Session, tenant_id, period_key: str) -> AccountingPeriod:
    period = session.scalar(select(AccountingPeriod).where(AccountingPeriod.tenant_id == tenant_id, AccountingPeriod.period_key == period_key))
    if period is None:
        year, month = period_key.split("-")
        period = AccountingPeriod(
            tenant_id=tenant_id,
            period_key=period_key,
            start_date=datetime(int(year), int(month), 1, tzinfo=timezone.utc),
            end_date=datetime(int(year), int(month), 1, tzinfo=timezone.utc),
        )
        session.add(period)
        session.flush()
    return period
