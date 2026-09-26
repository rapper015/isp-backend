import uuid
from datetime import datetime
from decimal import Decimal
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from .database import Base

class Plan(Base):
    __tablename__ = "plans"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    plan_code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    monthly_fee: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    download_rate_kbps: Mapped[int] = mapped_column()
    upload_rate_kbps: Mapped[int] = mapped_column()
    billing_cycle_days: Mapped[int] = mapped_column(Integer, default=30)
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PlanNetworkPolicyBinding(Base):
    """The immutable network-policy version sold with a BSS plan.

    Keeping this in a separate table makes the catalog extension additive for
    deployed databases and, crucially, pins a plan to an approved AAA version
    instead of relying on a policy name that may later change.
    """
    __tablename__ = "bss_plan_network_policy_bindings"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plans.id"), unique=True, index=True, nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True, nullable=False)
    policy_id: Mapped[uuid.UUID] = mapped_column(index=True, nullable=False)
    policy_version_id: Mapped[uuid.UUID] = mapped_column(unique=True, index=True, nullable=False)
    policy_code: Mapped[str] = mapped_column(String(128), nullable=False)
    policy_version: Mapped[int] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", nullable=False)
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

class Invoice(Base):
    __tablename__ = "invoices"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    invoice_number: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(index=True)  # CRM external reference
    billing_account_id: Mapped[uuid.UUID | None] = mapped_column(index=True, nullable=True)
    subscriber_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)  # OSS external reference
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plans.id"))
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    billing_period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    billing_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    line_items: Mapped[list] = mapped_column(JSON, default=list)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    balance_due: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    status: Mapped[str] = mapped_column(String(16), default="issued", index=True)
    due_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class BillingSchedule(Base):
    __tablename__ = "billing_schedules"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True, nullable=False)
    customer_id: Mapped[uuid.UUID] = mapped_column(index=True, nullable=False)
    subscriber_id: Mapped[uuid.UUID | None] = mapped_column(index=True, nullable=True)
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plans.id"), index=True, nullable=False)
    account_code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    cycle_days: Mapped[int] = mapped_column(Integer, default=30)
    due_days: Mapped[int] = mapped_column(Integer, default=7)
    tax_percent: Mapped[Decimal] = mapped_column(Numeric(6, 3), default=0)
    custom_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    next_invoice_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_invoice_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    auto_invoice: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class BillingRun(Base):
    __tablename__ = "billing_runs"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="running")
    generated_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class BillingAccount(Base):
    """Customer-owned financial account. One account can aggregate many services."""
    __tablename__ = "billing_accounts"
    __table_args__ = (UniqueConstraint("tenant_id", "customer_id", name="uq_billing_account_customer"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True, nullable=False)
    customer_id: Mapped[uuid.UUID] = mapped_column(index=True, nullable=False)
    account_number: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    cycle_days: Mapped[int] = mapped_column(Integer, default=30)
    due_days: Mapped[int] = mapped_column(Integer, default=7)
    tax_percent: Mapped[Decimal] = mapped_column(Numeric(6, 3), default=0)
    next_invoice_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_invoice_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    auto_invoice: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class BillingAccountItem(Base):
    """A billable subscriber connection attached to its customer's account."""
    __tablename__ = "billing_account_items"
    __table_args__ = (UniqueConstraint("billing_account_id", "subscriber_id", name="uq_billing_item_subscriber"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    billing_account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("billing_accounts.id", ondelete="CASCADE"), index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True, nullable=False)
    customer_id: Mapped[uuid.UUID] = mapped_column(index=True, nullable=False)
    subscriber_id: Mapped[uuid.UUID] = mapped_column(index=True, nullable=False)
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plans.id"), index=True, nullable=False)
    description: Mapped[str] = mapped_column(String(255), default="Internet service")
    custom_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    effective_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    payment_reference: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    invoice_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("invoices.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    method: Mapped[str] = mapped_column(String(32))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InvoiceImportBatch(Base):
    __tablename__ = "bss_invoice_import_batches"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True)
    franchise_id: Mapped[str] = mapped_column(String(64), index=True)
    file_name: Mapped[str] = mapped_column(String(255))
    file_hash: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(16), default="VALIDATED", index=True)
    options: Mapped[dict] = mapped_column(JSON, default=dict)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class InvoiceImportRow(Base):
    __tablename__ = "bss_invoice_import_rows"
    __table_args__ = (UniqueConstraint("batch_id", "source_row_number", name="uq_bss_invoice_import_row"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    batch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_invoice_import_batches.id", ondelete="CASCADE"), index=True)
    source_row_number: Mapped[int] = mapped_column(Integer)
    source_invoice_number: Mapped[str] = mapped_column(String(128), default="")
    username: Mapped[str] = mapped_column(String(128), default="")
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict)
    normalized_data: Mapped[dict] = mapped_column(JSON, default=dict)
    action: Mapped[str] = mapped_column(String(8), index=True)
    validation_errors: Mapped[list] = mapped_column(JSON, default=list)
    processing_error: Mapped[str] = mapped_column(Text, default="")
    target_invoice_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
