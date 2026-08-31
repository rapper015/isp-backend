"""M4 money handling and immutable ledger."""
from decimal import Decimal

import pytest

from app.revenue.ledger import ensure_account, post_entry, rebuild_projection, reverse_entry
from app.revenue.money import MoneyAmount, MoneyError, money
from app.revenue.models import JournalEntry, JournalLine, LedgerBalanceProjection


def test_money_rejects_float():
    with pytest.raises(MoneyError):
        money(10.5)


def test_money_quantizes_decimal():
    assert money("10.555") == Decimal("10.56")
    assert money("10.5") == Decimal("10.50")


def test_money_amount_cross_currency_rejected():
    a = MoneyAmount(Decimal("10.00"), "INR")
    b = MoneyAmount(Decimal("10.00"), "USD")
    with pytest.raises(MoneyError):
        a + b


def test_ledger_rejects_unbalanced(session, tenant):
    with pytest.raises(ValueError):
        post_entry(
            session,
            tenant.id,
            entry_type="ADJUSTMENT",
            currency="INR",
            lines=[("A", "DEBIT", "100.00"), ("B", "CREDIT", "99.00")],
            correlation_id="c1",
            description="unbalanced",
        )


def test_ledger_rejects_single_line(session, tenant):
    with pytest.raises(ValueError):
        post_entry(session, tenant.id, entry_type="ADJUSTMENT", currency="INR", lines=[("A", "DEBIT", "100.00")], correlation_id="c1", description="single line")


def test_ledger_balanced_entry_posts(session, tenant):
    entry = post_entry(
        session,
        tenant.id,
        entry_type="PAYMENT_CAPTURE",
        currency="INR",
        lines=[("AR_GATEWAY", "DEBIT", "500.00"), ("PAYMENT_INCOME", "CREDIT", "500.00")],
        correlation_id="c1",
        description="capture test",
    )
    session.commit()
    lines = list(session.scalars(JournalLine.__table__.select().where(JournalLine.entry_id == entry.id))) if False else list(session.scalars(__import__("sqlalchemy").select(JournalLine).where(JournalLine.entry_id == entry.id)))
    assert len(lines) == 2
    assert sum(line.debit for line in lines) == sum(line.credit for line in lines)


def test_ledger_immutable_and_reversal(session, tenant):
    entry = post_entry(session, tenant.id, entry_type="PAYMENT_CAPTURE", currency="INR", lines=[("AR_GATEWAY", "DEBIT", "500.00"), ("PAYMENT_INCOME", "CREDIT", "500.00")], correlation_id="c1", description="capture")
    session.commit()
    original = session.get(JournalEntry, entry.id)
    reversal = reverse_entry(session, tenant.id, original.entry_number, actor="tester", reason="correction", correlation_id="c2")
    session.commit()
    assert reversal.id != original.id
    assert reversal.reversal_of == original.id
    assert original.entry_type == "PAYMENT_CAPTURE"  # original untouched


def test_projection_rebuild(session, tenant):
    post_entry(session, tenant.id, entry_type="PAYMENT_CAPTURE", currency="INR", lines=[("AR_GATEWAY", "DEBIT", "700.00"), ("PAYMENT_INCOME", "CREDIT", "700.00")], correlation_id="c1", description="capture")
    session.commit()
    balances = rebuild_projection(session, tenant.id, __import__("app.revenue.ledger", fromlist=["period_key_for"]).period_key_for())
    assert balances
    proj = session.scalar(__import__("sqlalchemy").select(LedgerBalanceProjection).where(LedgerBalanceProjection.tenant_id == tenant.id))
    assert proj is not None
