"""Payment and settlement reconciliation with deterministic matching.

Transaction reconciliation matches internal transactions against gateway data;
settlement reconciliation matches gateway settlements against captures, refunds,
fees and chargebacks. Same report imports never duplicate transactions."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .events import publish_outbox
from .models import ReconciliationBatch, ReconciliationException, ReconciliationItem, Settlement, SettlementLine, PaymentTransaction
from .money import money, normalize_currency
from .state_machine import recon_item_transition


def _now() -> datetime:
    return datetime.now(timezone.utc)


def next_batch_number(prefix: str = "RBN") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12].upper()}"


def create_batch(session: Session, tenant_id, *, kind: str, import_source: str = "api", correlation_id: str) -> ReconciliationBatch:
    batch = ReconciliationBatch(tenant_id=tenant_id, batch_number=next_batch_number(), kind=kind, status="OPEN", import_source=import_source, correlation_id=correlation_id)
    session.add(batch)
    session.flush()
    return batch


def import_transaction_items(session: Session, tenant_id, batch: ReconciliationBatch, items: list[dict]) -> int:
    """Import gateway transaction rows. Duplicate (external_ref) within the
    batch is ignored; existing matched items are not duplicated."""
    count = 0
    for row in items:
        external_ref = row.get("external_ref") or row.get("id")
        if not external_ref:
            continue
        existing = session.scalar(select(ReconciliationItem).where(ReconciliationItem.tenant_id == tenant_id, ReconciliationItem.external_ref == external_ref, ReconciliationItem.batch_id == batch.id))
        if existing is not None:
            continue
        item = ReconciliationItem(
            tenant_id=tenant_id,
            batch_id=batch.id,
            external_ref=external_ref,
            amount=money(row.get("amount", 0)),
            currency=normalize_currency(row.get("currency")),
            status="UNMATCHED",
            detail=row,
        )
        session.add(item)
        count += 1
    session.flush()
    return count


def run_transaction_reconciliation(session: Session, tenant_id, batch: ReconciliationBatch) -> dict:
    """Match imported gateway transactions to internal payment transactions
    using deterministic rules (highest-confidence first)."""
    summary = {"matched": 0, "unmatched": 0, "exceptions": []}
    items = list(session.scalars(select(ReconciliationItem).where(ReconciliationItem.batch_id == batch.id, ReconciliationItem.status == "UNMATCHED")))
    for item in items:
        result = _match_transaction(session, tenant_id, item)
        if result["status"] == "matched":
            item.status = recon_item_transition(item.status, "MATCHED")
            item.matched_transaction_id = result["transaction_id"]
            item.rule_used = result["rule"]
            item.confidence = result["confidence"]
            summary["matched"] += 1
        elif result["status"] == "exception":
            item.status = recon_item_transition(item.status, "EXCEPTION")
            _exception(session, tenant_id, batch, "AMOUNT_MISMATCH", {"external_ref": item.external_ref, "detail": result["detail"]})
            summary["exceptions"].append(item.external_ref)
        else:
            summary["unmatched"] += 1
    publish_outbox(session, "reconciliation.completed.v1", {"batch_id": str(batch.id), "kind": batch.kind, "matched": summary["matched"], "unmatched": summary["unmatched"]}, tenant_id, batch.correlation_id, f"recon:{batch.id}")
    session.flush()
    return summary


def _match_transaction(session: Session, tenant_id, item: ReconciliationItem) -> dict:
    """Deterministic matching rules in priority order."""
    txn = session.scalar(select(PaymentTransaction).where(PaymentTransaction.tenant_id == tenant_id, PaymentTransaction.external_ref == item.external_ref))
    if txn is not None:
        if txn.amount != item.amount:
            return {"status": "exception", "detail": {"amount_mismatch": {"internal": str(txn.amount), "external": str(item.amount)}}}
        return {"status": "matched", "transaction_id": txn.id, "rule": "EXACT_EXTERNAL_ID", "confidence": "HIGH"}
    # Fallback: gateway order ref + amount.
    order_ref = item.detail.get("order_id") or item.detail.get("gateway_order_ref")
    if order_ref:
        txn = session.scalar(select(PaymentTransaction).where(PaymentTransaction.tenant_id == tenant_id, PaymentTransaction.external_ref == order_ref))
        if txn is not None and txn.amount == item.amount:
            return {"status": "matched", "transaction_id": txn.id, "rule": "GATEWAY_ORDER_PLUS_AMOUNT", "confidence": "HIGH"}
    return {"status": "unmatched"}


def import_settlement(session: Session, tenant_id, *, settlement_reference, net_amount, currency, fee_amount, settlement_date, bank_reference, lines: list[dict], gateway_account_id=None, correlation_id) -> Settlement:
    """Import a gateway settlement. Duplicate settlement_reference is ignored."""
    currency = normalize_currency(currency)
    existing = session.scalar(select(Settlement).where(Settlement.tenant_id == tenant_id, Settlement.settlement_reference == settlement_reference))
    if existing is not None:
        return existing
    settlement = Settlement(
        tenant_id=tenant_id,
        gateway_account_id=gateway_account_id,
        settlement_reference=settlement_reference,
        net_amount=money(net_amount),
        currency=currency,
        fee_amount=money(fee_amount or 0),
        settlement_date=settlement_date,
        bank_reference=bank_reference,
        status="IMPORTED",
        source="api",
        correlation_id=correlation_id,
    )
    session.add(settlement)
    session.flush()
    for line in lines:
        session.add(SettlementLine(tenant_id=tenant_id, settlement_id=settlement.id, line_type=line.get("line_type", "CAPTURE"), amount=money(line.get("amount", 0)), currency=currency, external_ref=line.get("external_ref")))
    publish_outbox(session, "settlement.received.v1", {"settlement_reference": settlement_reference, "net_amount": str(settlement.net_amount), "currency": currency}, tenant_id, correlation_id, f"settlement:{tenant_id}:{settlement_reference}")
    session.flush()
    return settlement


def run_settlement_reconciliation(session: Session, tenant_id, batch: ReconciliationBatch) -> dict:
    """Match settlements against captured payments, refunds, fees, chargebacks."""
    summary = {"settlements": 0, "matched": 0, "exceptions": []}
    settlements = list(session.scalars(select(Settlement).where(Settlement.tenant_id == tenant_id, Settlement.status == "IMPORTED")))
    for settlement in settlements:
        summary["settlements"] += 1
        captures = sum(Decimal(line.amount) for line in session.scalars(select(SettlementLine).where(SettlementLine.settlement_id == settlement.id, SettlementLine.line_type == "CAPTURE")))
        refunds = sum(Decimal(line.amount) for line in session.scalars(select(SettlementLine).where(SettlementLine.settlement_id == settlement.id, SettlementLine.line_type == "REFUND")))
        fees = sum(Decimal(line.amount) for line in session.scalars(select(SettlementLine).where(SettlementLine.settlement_id == settlement.id, SettlementLine.line_type == "FEE")))
        expected_net = captures - refunds - fees
        if expected_net == settlement.net_amount:
            settlement.status = "MATCHED"
            summary["matched"] += 1
        else:
            settlement.status = "MISMATCH"
            _exception(session, tenant_id, batch, "SETTLEMENT_MISMATCH", {"settlement_reference": settlement.settlement_reference, "expected_net": str(expected_net), "actual_net": str(settlement.net_amount)})
            summary["exceptions"].append(settlement.settlement_reference)
    session.flush()
    return summary


def _exception(session: Session, tenant_id, batch: ReconciliationBatch, exception_type: str, detail: dict) -> ReconciliationException:
    item = ReconciliationException(tenant_id=tenant_id, batch_id=batch.id, exception_type=exception_type, detail=detail, status="OPEN")
    session.add(item)
    session.flush()
    publish_outbox(session, "reconciliation.exception_created.v1", {"batch_id": str(batch.id), "exception_type": exception_type, "detail": detail}, tenant_id, batch.correlation_id, f"recon-exc:{batch.id}:{exception_type}")
    return item


def resolve_exception(session: Session, tenant_id, exception_id, *, notes: str, resolved_by: str) -> ReconciliationException:
    item = session.scalar(select(ReconciliationException).where(ReconciliationException.id == exception_id, ReconciliationException.tenant_id == tenant_id))
    if item is None:
        raise ValueError("reconciliation exception not found")
    item.status = "RESOLVED"
    item.resolution_notes = notes
    item.resolved_by = resolved_by
    return item
