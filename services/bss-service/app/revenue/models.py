"""Milestone 4 — BSS revenue domain models.

Money is always Decimal (Numeric); currency is enforced. Ledger entries and
payment transactions are immutable once posted. All tables are tenant-scoped."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class Tenant(Base):
    __tablename__ = "bss_tenants"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="INR", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BillingAccount(Base, Timestamped):
    __tablename__ = "bss_billing_accounts"
    __table_args__ = (UniqueConstraint("tenant_id", "account_code", name="uq_bss_account_code"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_tenants.id"), index=True, nullable=False)
    account_code: Mapped[str] = mapped_column(String(64), nullable=False)
    customer_ref: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="INR", nullable=False)
    credit_balance: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE", nullable=False)
    holds: Mapped[list] = mapped_column(JSON, default=list, nullable=False)


class RevenueInvoice(Base, Timestamped):
    """Normalized invoice; status is DERIVED from financial amounts."""
    __tablename__ = "bss_invoices"
    __table_args__ = (UniqueConstraint("tenant_id", "invoice_number", name="uq_bss_invoice_number"), Index("ix_bss_invoice_account", "tenant_id", "billing_account_id", "status"))
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_tenants.id"), index=True, nullable=False)
    billing_account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_billing_accounts.id"), index=True, nullable=False)
    invoice_number: Mapped[str] = mapped_column(String(64), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="INR", nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    paid_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    written_off_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="ISSUED", nullable=False, index=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    due_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    plan_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    external_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    meta: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class InvoiceLineItem(Base):
    __tablename__ = "bss_invoice_lines"
    __table_args__ = (Index("ix_bss_invoice_line_invoice", "invoice_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_tenants.id"), index=True, nullable=False)
    invoice_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_invoices.id"), nullable=False)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    unit_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), default="service", nullable=False)


class PaymentIntent(Base, Timestamped):
    __tablename__ = "bss_payment_intents"
    __table_args__ = (UniqueConstraint("tenant_id", "idempotency_key", name="uq_bss_intent_idem"), Index("ix_bss_intent_account", "tenant_id", "billing_account_id", "status"))
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_tenants.id"), index=True, nullable=False)
    billing_account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_billing_accounts.id"), index=True, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="INR", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="CREATED", nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    gateway_account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bss_gateway_accounts.id"), nullable=True, index=True)
    gateway_order_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    invoice_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PaymentAttempt(Base, Timestamped):
    __tablename__ = "bss_payment_attempts"
    __table_args__ = (Index("ix_bss_attempt_intent", "payment_intent_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_tenants.id"), index=True, nullable=False)
    payment_intent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_payment_intents.id"), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="CREATED", nullable=False)
    method: Mapped[str | None] = mapped_column(String(32), nullable=True)
    mode: Mapped[str] = mapped_column(String(8), default="test", nullable=False)
    gateway_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)


class PaymentTransaction(Base, Timestamped):
    """Immutable record of a confirmed money movement / payment status."""
    __tablename__ = "bss_payment_transactions"
    __table_args__ = (UniqueConstraint("tenant_id", "external_ref", name="uq_bss_txn_external"), Index("ix_bss_txn_intent", "tenant_id", "payment_intent_id"))
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_tenants.id"), index=True, nullable=False)
    payment_intent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_payment_intents.id"), index=True, nullable=False)
    billing_account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_billing_accounts.id"), index=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(24), default="CAPTURE", nullable=False)  # CAPTURE | REFUND | CHARGEBACK
    external_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="CONFIRMED", nullable=False)
    gateway_account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bss_gateway_accounts.id"), nullable=True)
    method: Mapped[str | None] = mapped_column(String(32), nullable=True)
    mode: Mapped[str] = mapped_column(String(8), default="test", nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class PaymentAllocation(Base, Timestamped):
    __tablename__ = "bss_payment_allocations"
    __table_args__ = (UniqueConstraint("tenant_id", "transaction_id", "invoice_id", "reversal_of", name="uq_bss_allocation"), Index("ix_bss_alloc_invoice", "tenant_id", "invoice_id"))
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_tenants.id"), index=True, nullable=False)
    transaction_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_payment_transactions.id"), index=True, nullable=False)
    invoice_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_invoices.id"), index=True, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    reversal_of: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)


class CreditNote(Base, Timestamped):
    __tablename__ = "bss_credit_notes"
    __table_args__ = (UniqueConstraint("tenant_id", "credit_note_number", name="uq_bss_credit_note"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_tenants.id"), index=True, nullable=False)
    billing_account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_billing_accounts.id"), index=True, nullable=False)
    credit_note_number: Mapped[str] = mapped_column(String(64), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    remaining: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), default="CREDIT", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="OPEN", nullable=False)


class Receipt(Base):
    __tablename__ = "bss_receipts"
    __table_args__ = (UniqueConstraint("tenant_id", "receipt_number", name="uq_bss_receipt_number"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_tenants.id"), index=True, nullable=False)
    billing_account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_billing_accounts.id"), index=True, nullable=False)
    transaction_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_payment_transactions.id"), nullable=False)
    receipt_number: Mapped[str] = mapped_column(String(64), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class GatewayAccount(Base, Timestamped):
    __tablename__ = "bss_gateway_accounts"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_bss_gateway_code"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_tenants.id"), index=True, nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    gateway_code: Mapped[str] = mapped_column(String(32), nullable=False)
    mode: Mapped[str] = mapped_column(String(8), default="test", nullable=False)
    api_key_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    secret_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    webhook_secret_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="INR", nullable=False)
    methods: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", nullable=False)
    capabilities: Mapped[list] = mapped_column(JSON, default=list, nullable=False)


class GatewayWebhook(Base, Timestamped):
    __tablename__ = "bss_gateway_webhooks"
    __table_args__ = (UniqueConstraint("tenant_id", "external_event_id", name="uq_bss_webhook_event"), Index("ix_bss_webhook_status", "tenant_id", "status"))
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_tenants.id"), index=True, nullable=False)
    gateway_account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bss_gateway_accounts.id"), nullable=True, index=True)
    external_event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    signature_valid: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    raw_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    redacted_payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="RECEIVED", nullable=False)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LedgerAccount(Base, Timestamped):
    __tablename__ = "bss_ledger_accounts"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_bss_ledger_code"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_tenants.id"), index=True, nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # ASSET | LIABILITY | EQUITY | REVENUE | EXPENSE
    currency: Mapped[str] = mapped_column(String(8), default="INR", nullable=False)
    normal_balance: Mapped[str] = mapped_column(String(8), default="DEBIT", nullable=False)


class JournalEntry(Base, Timestamped):
    """Immutable double-entry posting. Never edited or deleted."""
    __tablename__ = "bss_journal_entries"
    __table_args__ = (UniqueConstraint("tenant_id", "entry_number", name="uq_bss_journal_entry"), Index("ix_bss_journal_period", "tenant_id", "period_key"))
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_tenants.id"), index=True, nullable=False)
    entry_number: Mapped[str] = mapped_column(String(64), nullable=False)
    entry_type: Mapped[str] = mapped_column(String(32), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    period_key: Mapped[str] = mapped_column(String(16), nullable=False)
    effective_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    source_event: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    actor: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reversal_of: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bss_journal_entries.id"), nullable=True, index=True)


class JournalLine(Base):
    __tablename__ = "bss_journal_lines"
    __table_args__ = (Index("ix_bss_journal_line_entry", "entry_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_tenants.id"), index=True, nullable=False)
    entry_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_journal_entries.id"), nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_ledger_accounts.id"), nullable=False)
    debit: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    credit: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)


class AccountingPeriod(Base):
    __tablename__ = "bss_accounting_periods"
    __table_args__ = (UniqueConstraint("tenant_id", "period_key", name="uq_bss_period"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_tenants.id"), index=True, nullable=False)
    period_key: Mapped[str] = mapped_column(String(16), nullable=False)
    start_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="OPEN", nullable=False)


class LedgerBalanceProjection(Base, Timestamped):
    """Derived balance; rebuilt from journal lines, never mutated directly."""
    __tablename__ = "bss_ledger_balances"
    __table_args__ = (UniqueConstraint("tenant_id", "account_id", "period_key", name="uq_bss_ledger_balance"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_tenants.id"), index=True, nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_ledger_accounts.id"), nullable=False)
    period_key: Mapped[str] = mapped_column(String(16), nullable=False)
    balance: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)


class Refund(Base, Timestamped):
    __tablename__ = "bss_refunds"
    __table_args__ = (UniqueConstraint("tenant_id", "refund_reference", name="uq_bss_refund_ref"), Index("ix_bss_refund_txn", "tenant_id", "transaction_id"))
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_tenants.id"), index=True, nullable=False)
    billing_account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_billing_accounts.id"), index=True, nullable=False)
    transaction_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_payment_transactions.id"), nullable=False)
    refund_reference: Mapped[str] = mapped_column(String(64), nullable=False)
    gateway_refund_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", nullable=False)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)


class Dispute(Base, Timestamped):
    __tablename__ = "bss_disputes"
    __table_args__ = (UniqueConstraint("tenant_id", "gateway_dispute_ref", name="uq_bss_dispute_ref"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_tenants.id"), index=True, nullable=False)
    billing_account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_billing_accounts.id"), index=True, nullable=False)
    transaction_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_payment_transactions.id"), nullable=False)
    gateway_dispute_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="OPEN", nullable=False)
    evidence_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)


class ManualPayment(Base, Timestamped):
    __tablename__ = "bss_manual_payments"
    __table_args__ = (UniqueConstraint("tenant_id", "reference_number", name="uq_bss_manual_ref"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_tenants.id"), index=True, nullable=False)
    billing_account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_billing_accounts.id"), index=True, nullable=False)
    reference_number: Mapped[str] = mapped_column(String(64), nullable=False)
    method: Mapped[str] = mapped_column(String(32), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    external_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    payment_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deposited_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    collector: Mapped[str | None] = mapped_column(String(128), nullable=True)
    branch_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="DRAFT", nullable=False)
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    approval_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    submitted_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)


class Settlement(Base, Timestamped):
    __tablename__ = "bss_settlements"
    __table_args__ = (UniqueConstraint("tenant_id", "settlement_reference", name="uq_bss_settlement_ref"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_tenants.id"), index=True, nullable=False)
    gateway_account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bss_gateway_accounts.id"), nullable=True)
    settlement_reference: Mapped[str] = mapped_column(String(128), nullable=False)
    net_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    fee_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    settlement_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    bank_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="IMPORTED", nullable=False)
    source: Mapped[str] = mapped_column(String(32), default="api", nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)


class SettlementLine(Base):
    __tablename__ = "bss_settlement_lines"
    __table_args__ = (Index("ix_bss_settlement_line_settlement", "settlement_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_tenants.id"), index=True, nullable=False)
    settlement_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_settlements.id"), nullable=False)
    line_type: Mapped[str] = mapped_column(String(24), nullable=False)  # CAPTURE | REFUND | CHARGEBACK | FEE | TAX | ADJUSTMENT
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    external_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)


class ReconciliationBatch(Base, Timestamped):
    __tablename__ = "bss_recon_batches"
    __table_args__ = (UniqueConstraint("tenant_id", "batch_number", name="uq_bss_recon_batch"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_tenants.id"), index=True, nullable=False)
    batch_number: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # TRANSACTION | SETTLEMENT
    status: Mapped[str] = mapped_column(String(16), default="OPEN", nullable=False)
    import_source: Mapped[str] = mapped_column(String(32), default="api", nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ReconciliationItem(Base, Timestamped):
    __tablename__ = "bss_recon_items"
    __table_args__ = (Index("ix_bss_recon_item_status", "tenant_id", "status"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_tenants.id"), index=True, nullable=False)
    batch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_recon_batches.id"), index=True, nullable=False)
    external_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="UNMATCHED", nullable=False)
    matched_transaction_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bss_payment_transactions.id"), nullable=True)
    rule_used: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confidence: Mapped[str | None] = mapped_column(String(16), nullable=True)
    detail: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    resolution_notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class ReconciliationException(Base, Timestamped):
    __tablename__ = "bss_recon_exceptions"
    __table_args__ = (Index("ix_bss_recon_exc_status", "tenant_id", "status"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_tenants.id"), index=True, nullable=False)
    batch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_recon_batches.id"), nullable=False)
    exception_type: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="OPEN", nullable=False)
    resolution_notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class DunningPolicy(Base, Timestamped):
    __tablename__ = "bss_dunning_policies"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_bss_dunning_code"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_tenants.id"), index=True, nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str] = mapped_column(String(16), default="DRAFT", nullable=False)
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)


class DunningPolicyVersion(Base, Timestamped):
    __tablename__ = "bss_dunning_policy_versions"
    __table_args__ = (UniqueConstraint("policy_id", "version", name="uq_bss_dunning_version"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_tenants.id"), index=True, nullable=False)
    policy_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_dunning_policies.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(16), default="DRAFT", nullable=False)
    params: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class DunningStage(Base):
    __tablename__ = "bss_dunning_stages"
    __table_args__ = (UniqueConstraint("policy_version_id", "stage_order", name="uq_bss_dunning_stage"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_tenants.id"), index=True, nullable=False)
    policy_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_dunning_policy_versions.id"), nullable=False)
    stage_order: Mapped[int] = mapped_column(Integer, nullable=False)
    stage_code: Mapped[str] = mapped_column(String(64), nullable=False)
    delay_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    action_type: Mapped[str] = mapped_column(String(16), default="NOTIFY", nullable=False)
    message_template: Mapped[str | None] = mapped_column(String(255), nullable=True)


class DunningCase(Base, Timestamped):
    __tablename__ = "bss_dunning_cases"
    __table_args__ = (UniqueConstraint("tenant_id", "billing_account_id", "policy_version_id", name="uq_bss_dunning_case"), Index("ix_bss_dunning_case_status", "tenant_id", "status"))
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_tenants.id"), index=True, nullable=False)
    billing_account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_billing_accounts.id"), nullable=False)
    policy_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_dunning_policy_versions.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="OPEN", nullable=False)
    current_stage_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    suspended: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DunningAction(Base):
    __tablename__ = "bss_dunning_actions"
    __table_args__ = (Index("ix_bss_dunning_action_case", "case_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_tenants.id"), index=True, nullable=False)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_dunning_cases.id"), nullable=False)
    stage_order: Mapped[int] = mapped_column(Integer, nullable=False)
    action_type: Mapped[str] = mapped_column(String(16), nullable=False)
    trigger: Mapped[str] = mapped_column(String(32), nullable=False)
    result: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class PromiseToPay(Base, Timestamped):
    __tablename__ = "bss_promise_to_pay"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_tenants.id"), index=True, nullable=False)
    billing_account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_billing_accounts.id"), index=True, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    promise_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class CollectionHold(Base, Timestamped):
    __tablename__ = "bss_collection_holds"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_tenants.id"), index=True, nullable=False)
    billing_account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_billing_accounts.id"), index=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class OutboxEvent(Base):
    __tablename__ = "bss_outbox"
    __table_args__ = (Index("ix_bss_outbox_published", "published_at"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(index=True, nullable=True)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class InboxMessage(Base):
    __tablename__ = "bss_inbox"
    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    consumer: Mapped[str] = mapped_column(String(128), primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


# Master Spec Batch 5: monetization & catalog models (register on metadata).
from .catalog_models import (  # noqa: E402, F401
    ApiMarketplaceProduct,
    BudgetPlan,
    ChurnRecord,
    CommissionRecord,
    CostCenter,
    Coupon,
    EnterpriseCatalogItem,
    ExpenseRecord,
    FeatureAdoption,
    MarginOptimization,
    PartnerSlaMetric,
    ProductBundle,
    ProductStickiness,
    ProfitCenter,
    Redemption,
    Referral,
    ServiceCatalogItem,
    ServiceComposition,
    SlaPricingTier,
    TrialRecord,
    Vendor,
    WalletLedger,
)
