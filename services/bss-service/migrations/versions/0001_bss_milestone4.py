"""Add the Milestone 4 BSS revenue schema.

Creates all `bss_*` tables (billing accounts, invoices+lines, payment
intents/attempts/transactions/allocations, credit notes, receipts, gateway
accounts/webhooks, ledger, refunds, disputes, manual payments, settlements,
reconciliation, dunning) from SQLAlchemy metadata, following the additive
migration convention used by the other services. Legacy `plans`/`invoices`/
`payments` tables are preserved unchanged for compatibility.
"""
from alembic import op

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


def upgrade() -> None:
    from app.database import Base
    import app.revenue.models  # noqa: F401
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    from app.database import Base
    import app.revenue.models  # noqa: F401
    for name in _BSS_TABLES:
        Base.metadata.tables[name].drop(bind=op.get_bind(), checkfirst=True)
