"""M4 reconciliation: transaction matching, settlement reconciliation,
duplicate-import protection."""
import uuid
from decimal import Decimal

from app.revenue.models import ReconciliationBatch, Settlement, SettlementLine
from app.revenue.payments import capture_payment, create_payment_intent
from app.revenue.reconciliation import (
    create_batch,
    import_settlement,
    import_transaction_items,
    run_settlement_reconciliation,
    run_transaction_reconciliation,
)
from app.revenue.models import PaymentTransaction


def _capture(session, tenant, account, invoice, amount="500.00", ext=None):
    intent = create_payment_intent(session, tenant.id, billing_account_id=account.id, idempotency_key=f"rc-{uuid.uuid4().hex}", correlation_id="c1", invoice_ids=[invoice.id])
    session.commit()
    return capture_payment(session, tenant.id, intent_id=intent.id, external_ref=ext or f"ext-{uuid.uuid4().hex[:8]}", amount=Decimal(amount), currency="INR", idempotency_key=f"cap-{uuid.uuid4().hex}", correlation_id="c1")


def test_transaction_reconciliation_exact_match(session, tenant, account, invoice):
    txn = _capture(session, tenant, account, invoice, ext="ext-recon-1")
    session.commit()
    batch = create_batch(session, tenant.id, kind="TRANSACTION", correlation_id="c1")
    imported = import_transaction_items(session, tenant.id, batch, [{"external_ref": "ext-recon-1", "amount": 500, "currency": "INR"}])
    session.commit()
    assert imported == 1
    summary = run_transaction_reconciliation(session, tenant.id, batch)
    session.commit()
    assert summary["matched"] == 1
    item = session.query(__import__("app.revenue.models", fromlist=["ReconciliationItem"]).ReconciliationItem).filter(__import__("app.revenue.models", fromlist=["ReconciliationItem"]).ReconciliationItem.tenant_id == tenant.id).one()
    assert item.status == "MATCHED"
    assert item.rule_used == "EXACT_EXTERNAL_ID"


def test_transaction_amount_mismatch_creates_exception(session, tenant, account, invoice):
    _capture(session, tenant, account, invoice, amount="500.00", ext="ext-mismatch")
    session.commit()
    batch = create_batch(session, tenant.id, kind="TRANSACTION", correlation_id="c1")
    import_transaction_items(session, tenant.id, batch, [{"external_ref": "ext-mismatch", "amount": 999, "currency": "INR"}])
    session.commit()
    summary = run_transaction_reconciliation(session, tenant.id, batch)
    session.commit()
    assert summary["exceptions"]
    from app.revenue.models import ReconciliationException

    assert session.query(ReconciliationException).filter(ReconciliationException.tenant_id == tenant.id, ReconciliationException.exception_type == "AMOUNT_MISMATCH").count() == 1


def test_duplicate_report_import_is_safe(session, tenant, account, invoice):
    _capture(session, tenant, account, invoice, ext="ext-dup-report")
    session.commit()
    batch = create_batch(session, tenant.id, kind="TRANSACTION", correlation_id="c1")
    rows = [{"external_ref": "ext-dup-report", "amount": 500, "currency": "INR"}]
    first = import_transaction_items(session, tenant.id, batch, rows)
    second = import_transaction_items(session, tenant.id, batch, rows)
    session.commit()
    assert first == 1
    assert second == 0  # no duplicate item


def test_settlement_reconciliation_matches_net(session, tenant, account, invoice, gateway):
    _capture(session, tenant, account, invoice, amount="1000.00", ext="ext-stl")
    session.commit()
    settlement = import_settlement(
        session,
        tenant.id,
        settlement_reference="STL-1",
        net_amount=Decimal("965.00"),
        currency="INR",
        fee_amount=Decimal("35.00"),
        settlement_date=None,
        bank_reference="UTR-STL-1",
        lines=[
            {"line_type": "CAPTURE", "amount": 1000, "external_ref": "ext-stl"},
            {"line_type": "FEE", "amount": 35},
        ],
        gateway_account_id=gateway.id,
        correlation_id="c1",
    )
    session.commit()
    assert settlement.net_amount == Decimal("965.00")
    # Duplicate settlement import is ignored.
    again = import_settlement(session, tenant.id, settlement_reference="STL-1", net_amount=Decimal("965.00"), currency="INR", fee_amount=Decimal("35.00"), settlement_date=None, bank_reference="UTR-STL-1", lines=[], correlation_id="c1")
    session.commit()
    assert again.id == settlement.id
    batch = create_batch(session, tenant.id, kind="SETTLEMENT", correlation_id="c1")
    summary = run_settlement_reconciliation(session, tenant.id, batch)
    session.commit()
    assert summary["matched"] == 1
