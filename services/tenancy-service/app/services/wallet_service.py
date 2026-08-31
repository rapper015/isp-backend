"""Partner wallets backed by the immutable ledger. The displayed balance is a
rebuildable projection; every movement is an immutable WalletEntry that also
posts a balanced journal entry to the tenancy ledger."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.exceptions import NotFoundError, ValidationError
from ..domain.ledger import post_entry
from ..events import outbox
from ..models import WalletAccount, WalletEntry
from .audit_service import audit, correlation
from .organization_service import get_partner_or_404


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_wallet_or_404(session: Session, tenant_id, wallet_id) -> WalletAccount:
    wallet = session.get(WalletAccount, wallet_id)
    if wallet is None or wallet.tenant_id != tenant_id:
        raise NotFoundError("wallet not found")
    return wallet


def ensure_wallet(session: Session, tenant_id, partner_id: uuid.UUID, *, currency: str = "INR") -> WalletAccount:
    get_partner_or_404(session, tenant_id, partner_id)
    wallet = session.scalars(select(WalletAccount).where(
        WalletAccount.tenant_id == tenant_id, WalletAccount.partner_id == partner_id,
        WalletAccount.currency == currency)).first()
    if wallet is None:
        wallet = WalletAccount(tenant_id=tenant_id, partner_id=partner_id, currency=currency, balance=0.0)
        session.add(wallet)
        session.flush()
    return wallet


def post_wallet_entry(session: Session, tenant_id, wallet_id: uuid.UUID, *, entry_type: str,
                      amount: float, reference: str | None = None, reason: str | None = None,
                      actor: str = "system", correlation_id: str | None = None) -> WalletEntry:
    """Post an immutable wallet movement. amount is signed (+ credit / - debit).
    The wallet balance is updated as a projection and the movement is mirrored
    to the tenancy ledger."""
    request_id = correlation(correlation_id)
    wallet = get_wallet_or_404(session, tenant_id, wallet_id)
    if amount == 0:
        raise ValidationError("wallet entry amount must be non-zero")
    new_balance = wallet.balance + amount
    if new_balance < 0 and entry_type not in ("HOLD",):
        raise ValidationError("wallet balance cannot go negative")
    entry = WalletEntry(tenant_id=tenant_id, wallet_id=wallet.id, entry_type=entry_type,
                        amount=amount, balance_after=new_balance, reference=reference,
                        reason=reason, actor=actor, correlation_id=request_id)
    session.add(entry)
    wallet.balance = new_balance
    session.flush()
    # Mirror to the immutable tenancy ledger (balanced entry).
    debit_amount = amount if amount > 0 else 0.0
    credit_amount = -amount if amount < 0 else 0.0
    post_entry(session, tenant_id, entry_type="WALLET",
               lines=[{"account": "wallet_cash", "debit": debit_amount, "credit": credit_amount},
                      {"account": "partner_earnings", "debit": credit_amount, "credit": debit_amount}],
               reference=f"wallet:{entry.id}", posted_by=actor, correlation_id=request_id)
    audit(session, tenant_id, actor, "wallet.entry", resource_type="wallet",
          resource_id=wallet.id, after={"entry_type": entry_type, "amount": amount,
                                        "balance_after": new_balance}, reason=reason,
          correlation_id=request_id)
    outbox(session, "tenancy.wallet.entry.v1", tenant_id, request_id,
           {"tenant_id": str(tenant_id), "wallet_id": str(wallet.id), "entry_type": entry_type,
            "amount": amount, "balance_after": new_balance})
    return entry


def wallet_balance(session: Session, tenant_id, wallet_id: uuid.UUID) -> float:
    wallet = get_wallet_or_404(session, tenant_id, wallet_id)
    return wallet.balance


def rebuild_wallet(session: Session, tenant_id, wallet_id: uuid.UUID) -> WalletAccount:
    """Rebuild the balance projection from immutable entries."""
    wallet = get_wallet_or_404(session, tenant_id, wallet_id)
    entries = list(session.scalars(select(WalletEntry).where(WalletEntry.wallet_id == wallet.id)))
    wallet.balance = round(sum(e.amount for e in entries), 2)
    session.flush()
    return wallet
