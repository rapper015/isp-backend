"""Add the Milestone 4 BSS revenue schema.

Creates all `bss_*` tables (billing accounts, invoices+lines, payment
intents/attempts/transactions/allocations, credit notes, receipts, gateway
accounts/webhooks, ledger, refunds, disputes, manual payments, settlements,
reconciliation, dunning) from SQLAlchemy metadata, following the additive
migration convention used by the other services. Legacy `plans`/`invoices`/
`payments` tables are preserved unchanged for compatibility.
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_bss_milestone4"
down_revision = None
branch_labels = None
depends_on = None

_BSS_TABLES = (
    "bss_inbox",
    "bss_outbox",
    "bss_tenants",
    "bss_billing_accounts",
    "bss_invoices",
    "bss_invoice_lines",
    "bss_payment_intents",
    "bss_payment_attempts",
    "bss_payment_transactions",
    "bss_payment_allocations",
    "bss_credit_notes",
    "bss_receipts",
    "bss_gateway_accounts",
    "bss_gateway_webhooks",
    "bss_ledger_accounts",
    "bss_journal_entries",
    "bss_journal_lines",
    "bss_accounting_periods",
    "bss_ledger_balances",
    "bss_refunds",
    "bss_disputes",
    "bss_manual_payments",
    "bss_settlements",
    "bss_settlement_lines",
    "bss_recon_batches",
    "bss_recon_items",
    "bss_recon_exceptions",
    "bss_dunning_policies",
    "bss_dunning_policy_versions",
    "bss_dunning_stages",
    "bss_dunning_cases",
    "bss_dunning_actions",
    "bss_promise_to_pay",
    "bss_collection_holds",
)

_LEGACY_TABLES = ("plans", "invoices", "payments")


def upgrade() -> None:
    from app.database import Base
    import app.revenue.models  # noqa: F401

    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # These three tables predate Alembic in the BSS service.  Define their
    # original schema here instead of using the *current* application metadata;
    # otherwise a historical migration also creates tables/columns owned by
    # later revisions (0003-0008).
    if not inspector.has_table("plans"):
        op.create_table(
            "plans",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("plan_code", sa.String(64), nullable=False, unique=True, index=True),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("monthly_fee", sa.Numeric(12, 2), nullable=False),
            sa.Column("download_rate_kbps", sa.Integer(), nullable=False),
            sa.Column("upload_rate_kbps", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(16), nullable=False, server_default="active"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
    if not inspector.has_table("invoices"):
        op.create_table(
            "invoices",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("invoice_number", sa.String(64), nullable=False, unique=True, index=True),
            sa.Column("customer_id", sa.Uuid(), nullable=False, index=True),
            sa.Column("subscriber_id", sa.Uuid(), nullable=True, index=True),
            sa.Column("plan_id", sa.Uuid(), sa.ForeignKey("plans.id"), nullable=False),
            sa.Column("amount", sa.Numeric(12, 2), nullable=False),
            sa.Column("balance_due", sa.Numeric(12, 2), nullable=False),
            sa.Column("status", sa.String(16), nullable=False, server_default="issued", index=True),
            sa.Column("due_date", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
    if not inspector.has_table("payments"):
        op.create_table(
            "payments",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("payment_reference", sa.String(64), nullable=False, unique=True, index=True),
            sa.Column("invoice_id", sa.Uuid(), sa.ForeignKey("invoices.id"), nullable=False, index=True),
            sa.Column("amount", sa.Numeric(12, 2), nullable=False),
            sa.Column("method", sa.String(32), nullable=False),
            sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

    # Scope metadata creation to the tables owned by this revision.  Importing
    # app.models in migrations/env.py registers the latest schema on Base, so an
    # unscoped create_all would incorrectly pre-create future migration objects.
    Base.metadata.create_all(
        bind=bind,
        tables=[Base.metadata.tables[name] for name in _BSS_TABLES],
    )


def downgrade() -> None:
    from app.database import Base
    import app.revenue.models  # noqa: F401
    for name in reversed(_BSS_TABLES):
        Base.metadata.tables[name].drop(bind=op.get_bind(), checkfirst=True)
    for name in reversed(_LEGACY_TABLES):
        Base.metadata.tables[name].drop(bind=op.get_bind(), checkfirst=True)
