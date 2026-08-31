"""Commissions, revenue sharing, settlements, statements, disputes, payouts,
partner wallets and the internal immutable ledger (M4 pattern reused)."""
import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, Timestamped, UuidPk


# ---------------------------------------------------------------------------
# Commissions
# ---------------------------------------------------------------------------
class CommissionPlan(Base, Timestamped, UuidPk):
    __tablename__ = "ten_commission_plans"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_ten_commission_plan"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="DRAFT", nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CommissionPlanVersion(Base, Timestamped, UuidPk):
    __tablename__ = "ten_commission_plan_versions"
    __table_args__ = (UniqueConstraint("plan_id", "version", name="uq_ten_commission_plan_version"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    plan_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(24), default="DRAFT", nullable=False)
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    changed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class CommissionRule(Base, Timestamped, UuidPk):
    __tablename__ = "ten_commission_rules"
    __table_args__ = (UniqueConstraint("plan_id", "code", name="uq_ten_commission_rule"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    plan_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    basis: Mapped[str] = mapped_column(String(40), nullable=False)  # COMMISSION_BASES
    calculation_type: Mapped[str] = mapped_column(String(40), nullable=False)
    rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    fixed_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="INR", nullable=False)
    tiers: Mapped[list] = mapped_column(JSON, default=list, nullable=False)  # [{min, max, rate}]
    slabs: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    exclusions: Mapped[list] = mapped_column(JSON, default=list, nullable=False)  # TAX|DISCOUNT|...
    threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    multiplier: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class CommissionAgreement(Base, Timestamped, UuidPk):
    __tablename__ = "ten_commission_agreements"
    __table_args__ = (UniqueConstraint("tenant_id", "partner_id", "plan_id", name="uq_ten_commission_agreement"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    partner_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    plan_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    plan_version_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="DRAFT", nullable=False)
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class CommissionEarning(Base, Timestamped, UuidPk):
    """Immutable recognized earning. Reversals/clawbacks are separate rows."""

    __tablename__ = "ten_commission_earnings"
    __table_args__ = (
        UniqueConstraint("tenant_id", "source_event_id", "rule_id", name="uq_ten_commission_earning"),
        Index("ix_ten_earning_partner", "partner_id"),
        Index("ix_ten_earning_state", "status"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    partner_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    agreement_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    rule_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    rule_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    source_event_id: Mapped[str] = mapped_column(String(120), nullable=False)
    source_event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    customer_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    service_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    invoice_ref: Mapped[str | None] = mapped_column(String(80), nullable=True)
    payment_ref: Mapped[str | None] = mapped_column(String(80), nullable=True)
    basis: Mapped[str] = mapped_column(String(40), nullable=False)
    basis_amount: Mapped[float] = mapped_column(Float, nullable=False)
    rate_formula: Mapped[str] = mapped_column(String(160), nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="INR", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="RECOGNIZED", nullable=False)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    recognized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class CommissionAdjustment(Base, Timestamped, UuidPk):
    __tablename__ = "ten_commission_adjustments"

    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    earning_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)  # FINANCIAL_ADJUSTMENT_KINDS
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_event_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    actor: Mapped[str | None] = mapped_column(String(128), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class CommissionClawback(Base, Timestamped, UuidPk):
    __tablename__ = "ten_commission_clawbacks"
    __table_args__ = (UniqueConstraint("tenant_id", "earning_id", "source_event_id", name="uq_ten_clawback"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    earning_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)  # REFUND|CHARGEBACK|CANCELLATION|...
    source_event_id: Mapped[str] = mapped_column(String(120), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor: Mapped[str | None] = mapped_column(String(128), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class RevenueShareRule(Base, Timestamped, UuidPk):
    __tablename__ = "ten_revenue_share_rules"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_ten_revenue_share_rule"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    basis: Mapped[str] = mapped_column(String(40), nullable=False)
    split_type: Mapped[str] = mapped_column(String(24), default="PERCENTAGE", nullable=False)
    share: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="INR", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


# ---------------------------------------------------------------------------
# Settlements
# ---------------------------------------------------------------------------
class SettlementCycle(Base, Timestamped, UuidPk):
    __tablename__ = "ten_settlement_cycles"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_ten_settlement_cycle"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    state: Mapped[str] = mapped_column(String(24), default="DRAFT", nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="INR", nullable=False)


class PartnerSettlement(Base, Timestamped, UuidPk):
    __tablename__ = "ten_partner_settlements"
    __table_args__ = (
        UniqueConstraint("tenant_id", "partner_id", "cycle_id", name="uq_ten_partner_settlement"),
        Index("ix_ten_settlement_state", "state"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    partner_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    cycle_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    legal_entity_ref: Mapped[str | None] = mapped_column(String(80), nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="INR", nullable=False)
    opening_balance: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    total_earnings: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    total_adjustments: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    total_clawbacks: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    withholding: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    prior_advances: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    net_settlement: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    state: Mapped[str] = mapped_column(String(24), default="DRAFT", nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payout_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class SettlementLine(Base, Timestamped, UuidPk):
    __tablename__ = "ten_settlement_lines"
    __table_args__ = (
        UniqueConstraint("settlement_id", "source_event_id", name="uq_ten_settlement_line"),
        Index("ix_ten_settlement_line_earning", "earning_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    settlement_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    source_event_id: Mapped[str] = mapped_column(String(120), nullable=False)
    earning_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    line_type: Mapped[str] = mapped_column(String(24), default="EARNING", nullable=False)  # EARNING|ADJUSTMENT|CLAWBACK
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="INR", nullable=False)
    state: Mapped[str] = mapped_column(String(24), default="OPEN", nullable=False)


class SettlementDispute(Base, Timestamped, UuidPk):
    __tablename__ = "ten_settlement_disputes"

    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    settlement_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    line_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    state: Mapped[str] = mapped_column(String(24), default="OPEN", nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_ref: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    submitted_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    response: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    adjustment_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)


class SettlementPayout(Base, Timestamped, UuidPk):
    __tablename__ = "ten_settlement_payouts"

    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    settlement_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="INR", nullable=False)
    method: Mapped[str] = mapped_column(String(40), default="BANK_TRANSFER", nullable=False)
    reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    recorded_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SettlementReconciliation(Base, Timestamped, UuidPk):
    __tablename__ = "ten_settlement_reconciliations"

    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    settlement_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(24), default="RECONCILING", nullable=False)
    detail: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    reconciled_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PartnerStatement(Base, Timestamped, UuidPk):
    __tablename__ = "ten_partner_statements"
    __table_args__ = (UniqueConstraint("tenant_id", "settlement_id", name="uq_ten_partner_statement"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    settlement_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    partner_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    statement_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ---------------------------------------------------------------------------
# Wallets + immutable ledger
# ---------------------------------------------------------------------------
class WalletAccount(Base, Timestamped, UuidPk):
    __tablename__ = "ten_wallet_accounts"
    __table_args__ = (UniqueConstraint("tenant_id", "partner_id", "currency", name="uq_ten_wallet_account"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    partner_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    currency: Mapped[str] = mapped_column(String(8), default="INR", nullable=False)
    # Balance is a REBUILDABLE projection from immutable wallet entries.
    balance: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)


class WalletEntry(Base, Timestamped, UuidPk):
    __tablename__ = "ten_wallet_entries"
    __table_args__ = (Index("ix_ten_wallet_entry_account", "wallet_id"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    wallet_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    entry_type: Mapped[str] = mapped_column(String(40), nullable=False)  # WALLET_ENTRY_TYPES
    amount: Mapped[float] = mapped_column(Float, nullable=False)  # signed
    balance_after: Mapped[float] = mapped_column(Float, nullable=False)
    reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor: Mapped[str | None] = mapped_column(String(128), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class LedgerAccount(Base, Timestamped, UuidPk):
    __tablename__ = "ten_ledger_accounts"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_ten_ledger_account"),)

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), default="LIABILITY", nullable=False)


class JournalEntry(Base, Timestamped, UuidPk):
    __tablename__ = "ten_journal_entries"
    __table_args__ = (Index("ix_ten_journal_entry_number", "entry_number"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    entry_type: Mapped[str] = mapped_column(String(24), nullable=False)
    entry_number: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    reversal_of: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    posted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    posted_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class JournalLine(Base, Timestamped, UuidPk):
    __tablename__ = "ten_journal_lines"
    __table_args__ = (Index("ix_ten_journal_line_entry", "entry_id"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    entry_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    account_code: Mapped[str] = mapped_column(String(80), nullable=False)
    debit: Mapped[float] = mapped_column(Numeric(18, 2), default=0, nullable=False)
    credit: Mapped[float] = mapped_column(Numeric(18, 2), default=0, nullable=False)


class AccountingPeriod(Base, Timestamped, UuidPk):
    __tablename__ = "ten_accounting_periods"
    __table_args__ = (UniqueConstraint("tenant_id", "period_key", name="uq_ten_accounting_period"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    period_key: Mapped[str] = mapped_column(String(16), nullable=False)  # YYYY-MM
    state: Mapped[str] = mapped_column(String(24), default="OPEN", nullable=False)


class LedgerBalanceProjection(Base, Timestamped, UuidPk):
    __tablename__ = "ten_ledger_balance_projections"
    __table_args__ = (UniqueConstraint("tenant_id", "account_code", name="uq_ten_ledger_balance"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    account_code: Mapped[str] = mapped_column(String(80), nullable=False)
    balance: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
