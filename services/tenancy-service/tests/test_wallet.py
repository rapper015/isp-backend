"""Partner wallets backed by the immutable ledger."""
import pytest

from app.domain.exceptions import ValidationError
from app.models import WalletEntry
from app.services import wallet_service


def test_wallet_ledger_backed(session, tenant, make_partner):
    partner = make_partner()
    wallet = wallet_service.ensure_wallet(session, tenant.id, partner.id)
    assert wallet.balance == 0.0
    entry = wallet_service.post_wallet_entry(session, tenant.id, wallet.id, entry_type="DEPOSIT",
                                             amount=500.0, reason="opening deposit", actor="finance")
    session.commit()
    assert entry.balance_after == 500.0
    # The movement is mirrored to the immutable ledger.
    from app.models import JournalEntry

    entries = list(session.scalars(__import__("sqlalchemy").select(JournalEntry).where(
        JournalEntry.tenant_id == tenant.id, JournalEntry.entry_type == "WALLET")))
    assert len(entries) == 1


def test_wallet_balance_is_projection(session, tenant, make_partner):
    partner = make_partner()
    wallet = wallet_service.ensure_wallet(session, tenant.id, partner.id)
    wallet_service.post_wallet_entry(session, tenant.id, wallet.id, entry_type="CREDIT", amount=100.0,
                                     actor="system")
    wallet_service.post_wallet_entry(session, tenant.id, wallet.id, entry_type="DEBIT", amount=-40.0,
                                     actor="system")
    session.commit()
    assert wallet.balance == 60.0
    rebuilt = wallet_service.rebuild_wallet(session, tenant.id, wallet.id)
    assert rebuilt.balance == 60.0


def test_wallet_negative_balance_rejected(session, tenant, make_partner):
    partner = make_partner()
    wallet = wallet_service.ensure_wallet(session, tenant.id, partner.id)
    with pytest.raises(ValidationError):
        wallet_service.post_wallet_entry(session, tenant.id, wallet.id, entry_type="DEBIT",
                                         amount=-50.0, actor="finance")


def test_wallet_zero_entry_rejected(session, tenant, make_partner):
    partner = make_partner()
    wallet = wallet_service.ensure_wallet(session, tenant.id, partner.id)
    with pytest.raises(ValidationError):
        wallet_service.post_wallet_entry(session, tenant.id, wallet.id, entry_type="CREDIT",
                                         amount=0.0, actor="finance")


def test_cross_tenant_wallet_isolated(session, tenant, tenant_b, make_partner):
    from app.domain.exceptions import NotFoundError

    partner = make_partner()
    wallet = wallet_service.ensure_wallet(session, tenant.id, partner.id)
    # Tenant B cannot read tenant A's wallet.
    with pytest.raises(NotFoundError):
        wallet_service.get_wallet_or_404(session, tenant_b.id, wallet.id)
