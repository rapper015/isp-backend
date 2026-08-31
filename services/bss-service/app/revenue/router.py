"""BSS revenue API (Milestone 4). All routes are internal-service-authenticated
and tenant-scoped. No unrestricted status-update endpoints are exposed."""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..models import Invoice, Plan
from .cache import limited
from .dunning import (
    add_dunning_stage,
    advance_dunning_case,
    create_dunning_policy,
    open_dunning_case,
    pause_dunning_case,
    place_collection_hold,
    publish_dunning_policy,
    record_promise_to_pay,
    remove_collection_hold,
    resolve_dunning_case,
    resume_dunning_case,
)
from .enums import GATEWAY_CODES
from .events import publish_outbox
from .ledger import account_balances, rebuild_projection
from .manual_payments import (
    approve_manual_payment,
    create_manual_payment,
    post_manual_payment,
    reject_manual_payment,
    reverse_manual_payment,
    submit_manual_payment,
)
from .models import (
    BillingAccount,
    DunningCase,
    GatewayAccount,
    GatewayWebhook,
    InvoiceLineItem,
    JournalEntry,
    ManualPayment,
    PaymentAllocation,
    PaymentAttempt,
    PaymentIntent,
    PaymentTransaction,
    ReconciliationBatch,
    ReconciliationException,
    ReconciliationItem,
    Receipt,
    Refund,
    RevenueInvoice,
    Settlement,
    Tenant,
)
from .payments import (
    account_or_404,
    capture_payment,
    create_payment_intent,
    invoice_or_404,
    server_side_payable,
    start_hosted_checkout,
)
from .reconciliation import (
    create_batch,
    import_settlement,
    import_transaction_items,
    resolve_exception,
    run_settlement_reconciliation,
    run_transaction_reconciliation,
)
from .refunds import complete_refund, create_chargeback, create_refund, refundable_amount
from .reports import (
    chargeback_summary,
    credit_balance_report,
    daily_collections,
    invoice_aging,
    ledger_balances_report,
    outstanding_report,
    payment_method_summary,
    recon_exception_summary,
    refund_summary,
    settlement_summary,
)
from .schemas import (
    BillingAccountCreate,
    CaptureRequest,
    ChargebackCreate,
    DunningCaseAction,
    DunningPolicyCreate,
    DunningStageCreate,
    GatewayAccountCreate,
    HoldCreate,
    IntentCreate,
    InvoiceCreate,
    ManualPaymentAction,
    ManualPaymentCreate,
    PromiseCreate,
    ReconImport,
    ReconResolve,
    RefundCreate,
    SettlementImport,
    TenantCreate,
)
from .security import internal_service_auth
from .webhooks import list_webhooks, receive_webhook
from ..security import encrypt_secret

router = APIRouter(prefix="/api/bss", dependencies=[Depends(internal_service_auth)])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _tenant(session: Session, tenant_id) -> Tenant:
    tenant = session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(404, "tenant not found")
    return tenant


def _tenant_item(session: Session, model, item_id, tenant_id, label: str):
    item = session.scalar(select(model).where(model.id == item_id, model.tenant_id == tenant_id))
    if item is None:
        raise HTTPException(404, f"{label} not found")
    return item


def _correlation(value: str | None) -> str:
    return value or uuid.uuid4().hex


# ===========================================================================
# Tenant / billing accounts / invoices
# ===========================================================================

@router.post("/tenants", status_code=201)
def create_tenant(payload: TenantCreate, session: Session = Depends(db)):
    tenant = Tenant(name=payload.name, code=payload.code, currency=payload.currency)
    session.add(tenant)
    try:
        session.commit()
    except Exception as error:  # noqa: BLE001
        session.rollback()
        raise HTTPException(409, "tenant code already exists") from error
    return {"id": str(tenant.id), "code": tenant.code}


@router.post("/billing-accounts", status_code=201)
def create_billing_account(payload: BillingAccountCreate, session: Session = Depends(db)):
    _tenant(session, payload.tenant_id)
    account = BillingAccount(tenant_id=payload.tenant_id, account_code=payload.account_code, customer_ref=payload.customer_ref, currency=payload.currency, status="ACTIVE")
    session.add(account)
    try:
        session.commit()
    except Exception as error:  # noqa: BLE001
        session.rollback()
        raise HTTPException(409, "account code already exists") from error
    return {"id": str(account.id), "account_code": account.account_code, "customer_ref": account.customer_ref}


@router.get("/billing-accounts")
def list_billing_accounts(tenant_id: uuid.UUID, session: Session = Depends(db)):
    return [
        {"id": str(item.id), "account_code": item.account_code, "customer_ref": item.customer_ref, "currency": item.currency, "credit_balance": str(item.credit_balance), "status": item.status, "holds": item.holds}
        for item in session.scalars(select(BillingAccount).where(BillingAccount.tenant_id == tenant_id).order_by(BillingAccount.created_at))
    ]


@router.post("/invoices", status_code=201)
def create_invoice(payload: InvoiceCreate, session: Session = Depends(db)):
    account = account_or_404(session, payload.tenant_id, payload.billing_account_id)
    if account.currency != payload.currency:
        raise HTTPException(422, f"invoice currency {payload.currency} does not match account currency {account.currency}")
    invoice = RevenueInvoice(
        tenant_id=payload.tenant_id,
        billing_account_id=account.id,
        invoice_number=payload.invoice_number,
        currency=payload.currency,
        total_amount=payload.total_amount,
        paid_amount=Decimal("0.00"),
        written_off_amount=Decimal("0.00"),
        status="ISSUED",
        issued_at=_now(),
        due_date=payload.due_date,
        plan_reference=payload.plan_reference,
        external_reference=payload.external_reference,
    )
    session.add(invoice)
    session.flush()
    for line in payload.lines:
        session.add(
            InvoiceLineItem(
                tenant_id=payload.tenant_id,
                invoice_id=invoice.id,
                description=line.get("description", "service"),
                quantity=int(line.get("quantity", 1)),
                unit_amount=Decimal(str(line.get("unit_amount", payload.total_amount))),
                amount=Decimal(str(line.get("amount", payload.total_amount))),
                kind=line.get("kind", "service"),
            )
        )
    request_id = _correlation(None)
    publish_outbox(session, "invoice.issued.v1", {"invoice_id": str(invoice.id), "invoice_number": invoice.invoice_number, "billing_account_id": str(account.id), "amount": str(invoice.total_amount), "currency": invoice.currency}, payload.tenant_id, request_id, f"invoice-issued:{payload.tenant_id}:{invoice.invoice_number}")
    session.commit()
    return {"id": str(invoice.id), "invoice_number": invoice.invoice_number, "status": invoice.status, "balance_due": str(invoice.total_amount)}


@router.get("/invoices")
def list_invoices(tenant_id: uuid.UUID, billing_account_id: uuid.UUID | None = None, status: str | None = None, session: Session = Depends(db)):
    stmt = select(RevenueInvoice).where(RevenueInvoice.tenant_id == tenant_id)
    if billing_account_id:
        stmt = stmt.where(RevenueInvoice.billing_account_id == billing_account_id)
    if status:
        stmt = stmt.where(RevenueInvoice.status == status)
    return [_invoice_json(item) for item in session.scalars(stmt.order_by(RevenueInvoice.created_at.desc()).limit(200))]


@router.get("/invoices/{invoice_id}")
def invoice_detail(invoice_id: uuid.UUID, tenant_id: uuid.UUID, session: Session = Depends(db)):
    return _invoice_json(_tenant_item(session, RevenueInvoice, invoice_id, tenant_id, "invoice"))


def _invoice_json(item: RevenueInvoice) -> dict:
    return {
        "id": str(item.id),
        "invoice_number": item.invoice_number,
        "billing_account_id": str(item.billing_account_id),
        "currency": item.currency,
        "total_amount": str(item.total_amount),
        "paid_amount": str(item.paid_amount),
        "written_off_amount": str(item.written_off_amount),
        "balance_due": str(item.total_amount - item.paid_amount - item.written_off_amount),
        "status": item.status,
        "issued_at": item.issued_at,
        "due_date": item.due_date,
    }


# ===========================================================================
# Payment intents / capture / history
# ===========================================================================

@router.post("/payment-intents", status_code=201)
def create_intent(payload: IntentCreate, session: Session = Depends(db)):
    try:
        intent = create_payment_intent(
            session,
            payload.tenant_id,
            billing_account_id=payload.billing_account_id,
            amount=payload.amount,
            currency=payload.currency,
            invoice_ids=payload.invoice_ids,
            description=payload.description,
            idempotency_key=payload.idempotency_key,
            correlation_id=_correlation(None),
            gateway_account_id=payload.gateway_account_id,
            allow_overpayment=payload.allow_overpayment,
            created_by=payload.created_by,
        )
    except Exception as error:  # noqa: BLE001
        raise HTTPException(422, str(error)) from error
    session.commit()
    return {"id": str(intent.id), "amount": str(intent.amount), "currency": intent.currency, "status": intent.status, "idempotent": intent.idempotency_key == payload.idempotency_key}


@router.post("/payment-intents/{intent_id}/checkout")
def start_checkout(intent_id: uuid.UUID, tenant_id: uuid.UUID, session: Session = Depends(db)):
    try:
        intent, attempt, safe_payload = start_hosted_checkout(session, tenant_id, intent_id)
    except Exception as error:  # noqa: BLE001
        raise HTTPException(422, str(error)) from error
    session.commit()
    return {"payment_intent_id": str(intent.id), "status": intent.status, "attempt_id": str(attempt.id), "checkout": safe_payload}


@router.post("/payment-intents/{intent_id}/capture")
def capture(intent_id: uuid.UUID, payload: CaptureRequest, session: Session = Depends(db)):
    if payload.intent_id != intent_id:
        raise HTTPException(422, "intent mismatch")
    try:
        txn = capture_payment(
            session,
            payload.tenant_id,
            intent_id=intent_id,
            external_ref=payload.external_ref,
            amount=payload.amount,
            currency=payload.currency,
            method=payload.method,
            mode=payload.mode,
            idempotency_key=payload.idempotency_key,
            correlation_id=_correlation(None),
            gateway_account_id=payload.gateway_account_id,
        )
    except Exception as error:  # noqa: BLE001
        raise HTTPException(422, str(error)) from error
    session.commit()
    return {"transaction_id": str(txn.id), "external_ref": txn.external_ref, "amount": str(txn.amount), "status": txn.status}


@router.get("/payment-intents")
def list_intents(tenant_id: uuid.UUID, billing_account_id: uuid.UUID | None = None, session: Session = Depends(db)):
    stmt = select(PaymentIntent).where(PaymentIntent.tenant_id == tenant_id)
    if billing_account_id:
        stmt = stmt.where(PaymentIntent.billing_account_id == billing_account_id)
    return [
        {"id": str(item.id), "amount": str(item.amount), "currency": item.currency, "status": item.status, "gateway_order_ref": item.gateway_order_ref, "created_at": item.created_at}
        for item in session.scalars(stmt.order_by(PaymentIntent.created_at.desc()).limit(200))
    ]


@router.get("/payments")
def list_payments(tenant_id: uuid.UUID, billing_account_id: uuid.UUID | None = None, session: Session = Depends(db)):
    stmt = select(PaymentTransaction).where(PaymentTransaction.tenant_id == tenant_id)
    if billing_account_id:
        stmt = stmt.where(PaymentTransaction.billing_account_id == billing_account_id)
    return [
        {"id": str(item.id), "external_ref": item.external_ref, "kind": item.kind, "amount": str(item.amount), "currency": item.currency, "status": item.status, "method": item.method, "occurred_at": item.occurred_at}
        for item in session.scalars(stmt.order_by(PaymentTransaction.occurred_at.desc()).limit(200))
    ]


@router.get("/payments/{transaction_id}/allocations")
def payment_allocations(transaction_id: uuid.UUID, tenant_id: uuid.UUID, session: Session = Depends(db)):
    return [
        {"id": str(item.id), "invoice_id": str(item.invoice_id), "amount": str(item.amount), "currency": item.currency, "reversal_of": str(item.reversal_of) if item.reversal_of else None}
        for item in session.scalars(select(PaymentAllocation).where(PaymentAllocation.tenant_id == tenant_id, PaymentAllocation.transaction_id == transaction_id))
    ]


@router.get("/payments/{transaction_id}/receipt")
def payment_receipt(transaction_id: uuid.UUID, tenant_id: uuid.UUID, session: Session = Depends(db)):
    receipt = session.scalar(select(Receipt).where(Receipt.tenant_id == tenant_id, Receipt.transaction_id == transaction_id))
    if receipt is None:
        raise HTTPException(404, "receipt not found")
    return {"receipt_number": receipt.receipt_number, "amount": str(receipt.amount), "currency": receipt.currency, "issued_at": receipt.issued_at}


@router.get("/billing-accounts/{billing_account_id}/outstanding")
def outstanding(billing_account_id: uuid.UUID, tenant_id: uuid.UUID, session: Session = Depends(db)):
    account = account_or_404(session, tenant_id, billing_account_id)
    payable = server_side_payable(session, tenant_id, account)
    return {"billing_account_id": str(account.id), "payable": str(payable), "currency": account.currency, "credit_balance": str(account.credit_balance)}


# ===========================================================================
# Gateway accounts
# ===========================================================================

@router.post("/gateway-accounts", status_code=201)
def create_gateway_account(payload: GatewayAccountCreate, session: Session = Depends(db)):
    _tenant(session, payload.tenant_id)
    if payload.gateway_code not in GATEWAY_CODES:
        raise HTTPException(422, f"unsupported gateway {payload.gateway_code!r}")
    account = GatewayAccount(
        tenant_id=payload.tenant_id,
        code=payload.code,
        gateway_code=payload.gateway_code,
        mode=payload.mode,
        api_key_ciphertext=encrypt_secret(payload.api_key),
        secret_ciphertext=encrypt_secret(payload.secret),
        webhook_secret_ciphertext=encrypt_secret(payload.webhook_secret),
        currency=payload.currency,
        methods=payload.methods,
        is_default=payload.is_default,
        priority=payload.priority,
        status="ACTIVE",
    )
    session.add(account)
    session.commit()
    # Never return secrets.
    return {"id": str(account.id), "code": account.code, "gateway_code": account.gateway_code, "mode": account.mode, "is_default": account.is_default}


@router.get("/gateway-accounts")
def list_gateway_accounts(tenant_id: uuid.UUID, session: Session = Depends(db)):
    return [
        {"id": str(item.id), "code": item.code, "gateway_code": item.gateway_code, "mode": item.mode, "currency": item.currency, "methods": item.methods, "is_default": item.is_default, "priority": item.priority, "status": item.status}
        for item in session.scalars(select(GatewayAccount).where(GatewayAccount.tenant_id == tenant_id).order_by(GatewayAccount.priority))
    ]


# ===========================================================================
# Webhooks
# ===========================================================================

@router.post("/webhooks/gateway/{gateway_account_id}")
async def webhook_receive(gateway_account_id: uuid.UUID, request: Request, session: Session = Depends(db)):
    body_bytes = await request.body()
    raw_body = body_bytes.decode("utf-8")
    tenant_id = request.query_params.get("tenant_id")
    signature = request.headers.get("X-Razorpay-Signature") or request.headers.get("X-Signature") or ""
    external_event_id = request.headers.get("X-Event-Id") or uuid.uuid4().hex
    event_type = request.headers.get("X-Event-Type") or "payment.captured.v1"
    if not tenant_id:
        raise HTTPException(400, "tenant_id query parameter required")
    if not limited(f"webhook:{tenant_id}:{gateway_account_id}", int(__import__("os").getenv("BSS_WEBHOOK_RATE_LIMIT", "300")), 60):
        raise HTTPException(429, "rate limit exceeded")
    try:
        webhook = receive_webhook(
            session,
            uuid.UUID(tenant_id),
            gateway_account_id=gateway_account_id,
            raw_body=raw_body,
            signature=signature,
            external_event_id=external_event_id,
            event_type=event_type,
            correlation_id=_correlation(None),
        )
    except ValueError as error:
        session.commit()
        raise HTTPException(400, str(error)) from error
    session.commit()
    return {"status": "accepted", "webhook_id": str(webhook.id), "signature_valid": webhook.signature_valid, "processing_status": webhook.status}


@router.get("/webhooks")
def webhook_history(tenant_id: uuid.UUID, status: str | None = None, limit: int = 100, session: Session = Depends(db)):
    return [
        {"id": str(item.id), "external_event_id": item.external_event_id, "event_type": item.event_type, "signature_valid": item.signature_valid, "status": item.status, "raw_hash": item.raw_hash, "correlation_id": item.correlation_id, "received_at": item.received_at}
        for item in list_webhooks(session, tenant_id, status, limit)
    ]


# ===========================================================================
# Refunds / chargebacks
# ===========================================================================

@router.post("/refunds", status_code=201)
def create_refund_endpoint(payload: RefundCreate, session: Session = Depends(db)):
    try:
        refund = create_refund(
            session,
            payload.tenant_id,
            transaction_id=payload.transaction_id,
            amount=payload.amount,
            currency=payload.currency,
            reason=payload.reason,
            refund_reference=payload.refund_reference,
            correlation_id=_correlation(None),
            approved_by=payload.approved_by,
            requires_approval=payload.requires_approval,
        )
    except Exception as error:  # noqa: BLE001
        raise HTTPException(422, str(error)) from error
    session.commit()
    return {"id": str(refund.id), "refund_reference": refund.refund_reference, "status": refund.status, "amount": str(refund.amount)}


@router.post("/refunds/{refund_id}/approve")
def approve_refund(refund_id: uuid.UUID, tenant_id: uuid.UUID, session: Session = Depends(db)):
    try:
        refund = complete_refund(session, tenant_id, refund_id, correlation_id=_correlation(None))
    except Exception as error:  # noqa: BLE001
        raise HTTPException(422, str(error)) from error
    session.commit()
    return {"id": str(refund.id), "status": refund.status}


@router.get("/refunds")
def list_refunds(tenant_id: uuid.UUID, session: Session = Depends(db)):
    return [
        {"id": str(item.id), "refund_reference": item.refund_reference, "transaction_id": str(item.transaction_id), "amount": str(item.amount), "currency": item.currency, "status": item.status, "gateway_refund_id": item.gateway_refund_id}
        for item in session.scalars(select(Refund).where(Refund.tenant_id == tenant_id).order_by(Refund.created_at.desc()).limit(200))
    ]


@router.post("/chargebacks", status_code=201)
def create_chargeback_endpoint(payload: ChargebackCreate, session: Session = Depends(db)):
    try:
        dispute = create_chargeback(
            session,
            payload.tenant_id,
            transaction_id=payload.transaction_id,
            gateway_dispute_ref=payload.gateway_dispute_ref,
            amount=payload.amount,
            currency=payload.currency,
            reason=payload.reason,
            evidence_deadline=payload.evidence_deadline,
            correlation_id=_correlation(None),
        )
    except Exception as error:  # noqa: BLE001
        raise HTTPException(422, str(error)) from error
    session.commit()
    return {"id": str(dispute.id), "gateway_dispute_ref": dispute.gateway_dispute_ref, "status": dispute.status}


# ===========================================================================
# Manual payments
# ===========================================================================

@router.post("/manual-payments", status_code=201)
def create_manual(payload: ManualPaymentCreate, session: Session = Depends(db)):
    try:
        item = create_manual_payment(
            session,
            payload.tenant_id,
            billing_account_id=payload.billing_account_id,
            method=payload.method,
            amount=payload.amount,
            currency=payload.currency,
            external_reference=payload.external_reference,
            payment_date=payload.payment_date,
            collector=payload.collector,
            branch_reference=payload.branch_reference,
            evidence=payload.evidence,
            notes=payload.notes,
            reference_number=payload.reference_number,
            correlation_id=_correlation(payload.correlation_id),
        )
    except Exception as error:  # noqa: BLE001
        raise HTTPException(422, str(error)) from error
    session.commit()
    return {"id": str(item.id), "status": item.status, "requires_approval": item.requires_approval}


@router.post("/manual-payments/{manual_id}/submit")
def submit_manual(manual_id: uuid.UUID, payload: ManualPaymentAction, session: Session = Depends(db)):
    item = submit_manual_payment(session, payload.tenant_id, manual_id, submitted_by=payload.actor)
    session.commit()
    return {"id": str(item.id), "status": item.status}


@router.post("/manual-payments/{manual_id}/approve")
def approve_manual(manual_id: uuid.UUID, payload: ManualPaymentAction, session: Session = Depends(db)):
    item = approve_manual_payment(session, payload.tenant_id, manual_id, approved_by=payload.actor)
    session.commit()
    return {"id": str(item.id), "status": item.status}


@router.post("/manual-payments/{manual_id}/reject")
def reject_manual(manual_id: uuid.UUID, payload: ManualPaymentAction, session: Session = Depends(db)):
    item = reject_manual_payment(session, payload.tenant_id, manual_id, reason=payload.reason or "rejected")
    session.commit()
    return {"id": str(item.id), "status": item.status}


@router.post("/manual-payments/{manual_id}/post")
def post_manual(manual_id: uuid.UUID, payload: ManualPaymentAction, session: Session = Depends(db)):
    try:
        txn = post_manual_payment(session, payload.tenant_id, manual_id, correlation_id=_correlation(None))
    except Exception as error:  # noqa: BLE001
        raise HTTPException(422, str(error)) from error
    session.commit()
    return {"transaction_id": str(txn.id), "status": "POSTED"}


@router.post("/manual-payments/{manual_id}/reverse")
def reverse_manual(manual_id: uuid.UUID, payload: ManualPaymentAction, session: Session = Depends(db)):
    item = reverse_manual_payment(session, payload.tenant_id, manual_id, reversed_by=payload.actor, reason=payload.reason or "reversed", correlation_id=_correlation(None))
    session.commit()
    return {"id": str(item.id), "status": item.status}


# ===========================================================================
# Reconciliation
# ===========================================================================

@router.post("/reconciliation/batches", status_code=201)
def create_recon_batch(payload: ReconImport, session: Session = Depends(db)):
    batch = create_batch(session, payload.tenant_id, kind=payload.kind, import_source=payload.import_source, correlation_id=_correlation(None))
    imported = import_transaction_items(session, payload.tenant_id, batch, payload.items) if payload.kind == "TRANSACTION" else 0
    session.commit()
    return {"id": str(batch.id), "batch_number": batch.batch_number, "kind": batch.kind, "imported_items": imported}


@router.post("/reconciliation/batches/{batch_id}/run")
def run_recon(batch_id: uuid.UUID, tenant_id: uuid.UUID, session: Session = Depends(db)):
    batch = _tenant_item(session, ReconciliationBatch, batch_id, tenant_id, "reconciliation batch")
    if batch.kind == "TRANSACTION":
        summary = run_transaction_reconciliation(session, tenant_id, batch)
    else:
        summary = run_settlement_reconciliation(session, tenant_id, batch)
    session.commit()
    return {"batch_id": str(batch.id), **summary}


@router.post("/reconciliation/settlements", status_code=201)
def import_settlement_endpoint(payload: SettlementImport, session: Session = Depends(db)):
    settlement = import_settlement(
        session,
        payload.tenant_id,
        settlement_reference=payload.settlement_reference,
        net_amount=payload.net_amount,
        currency=payload.currency,
        fee_amount=payload.fee_amount,
        settlement_date=payload.settlement_date,
        bank_reference=payload.bank_reference,
        lines=payload.lines,
        gateway_account_id=payload.gateway_account_id,
        correlation_id=_correlation(None),
    )
    session.commit()
    return {"id": str(settlement.id), "settlement_reference": settlement.settlement_reference, "net_amount": str(settlement.net_amount)}


@router.get("/reconciliation/exceptions")
def list_recon_exceptions(tenant_id: uuid.UUID, status: str = "OPEN", session: Session = Depends(db)):
    return [
        {"id": str(item.id), "batch_id": str(item.batch_id), "exception_type": item.exception_type, "detail": item.detail, "status": item.status, "resolution_notes": item.resolution_notes}
        for item in session.scalars(select(ReconciliationException).where(ReconciliationException.tenant_id == tenant_id, ReconciliationException.status == status).order_by(ReconciliationException.created_at.desc()).limit(200))
    ]


@router.post("/reconciliation/exceptions/{exception_id}/resolve")
def resolve_recon_exception(exception_id: uuid.UUID, payload: ReconResolve, session: Session = Depends(db)):
    item = resolve_exception(session, payload.tenant_id, exception_id, notes=payload.notes, resolved_by=payload.resolved_by)
    session.commit()
    return {"id": str(item.id), "status": item.status}


# ===========================================================================
# Dunning
# ===========================================================================

@router.post("/dunning/policies", status_code=201)
def create_dunning(payload: DunningPolicyCreate, session: Session = Depends(db)):
    policy = create_dunning_policy(session, payload.tenant_id, code=payload.code, name=payload.name, params=payload.params)
    session.commit()
    return {"id": str(policy.id), "code": policy.code, "version_id": str(policy.current_version_id)}


@router.post("/dunning/stages", status_code=201)
def add_stage(payload: DunningStageCreate, session: Session = Depends(db)):
    stage = add_dunning_stage(session, payload.tenant_id, payload.policy_version_id, stage_order=payload.stage_order, stage_code=payload.stage_code, delay_seconds=payload.delay_seconds, action_type=payload.action_type, message_template=payload.message_template)
    session.commit()
    return {"id": str(stage.id), "stage_code": stage.stage_code, "stage_order": stage.stage_order}


@router.post("/dunning/policies/{policy_id}/publish")
def publish_dunning(policy_id: uuid.UUID, tenant_id: uuid.UUID, session: Session = Depends(db)):
    version = publish_dunning_policy(session, tenant_id, policy_id)
    session.commit()
    return {"version_id": str(version.id), "state": version.state}


@router.post("/dunning/cases", status_code=201)
def open_case(billing_account_id: uuid.UUID, tenant_id: uuid.UUID, policy_version_id: uuid.UUID, session: Session = Depends(db)):
    case = open_dunning_case(session, tenant_id, billing_account_id, policy_version_id, correlation_id=_correlation(None))
    session.commit()
    return {"id": str(case.id), "status": case.status, "current_stage_order": case.current_stage_order}


@router.post("/dunning/cases/{case_id}/advance")
def advance_case(case_id: uuid.UUID, payload: DunningCaseAction, session: Session = Depends(db)):
    case = advance_dunning_case(session, payload.tenant_id, case_id, correlation_id=_correlation(payload.correlation_id))
    session.commit()
    return {"id": str(case.id), "status": case.status, "current_stage_order": case.current_stage_order}


@router.post("/dunning/cases/{case_id}/pause")
def pause_case(case_id: uuid.UUID, payload: DunningCaseAction, session: Session = Depends(db)):
    case = pause_dunning_case(session, payload.tenant_id, case_id, actor=payload.actor)
    session.commit()
    return {"id": str(case.id), "status": case.status}


@router.post("/dunning/cases/{case_id}/resume")
def resume_case(case_id: uuid.UUID, payload: DunningCaseAction, session: Session = Depends(db)):
    case = resume_dunning_case(session, payload.tenant_id, case_id, actor=payload.actor)
    session.commit()
    return {"id": str(case.id), "status": case.status}


@router.post("/dunning/cases/{case_id}/resolve")
def resolve_case(case_id: uuid.UUID, payload: DunningCaseAction, session: Session = Depends(db)):
    case = resolve_dunning_case(session, payload.tenant_id, case_id, correlation_id=_correlation(payload.correlation_id))
    session.commit()
    return {"id": str(case.id), "status": case.status}


@router.get("/dunning/cases")
def list_dunning_cases(tenant_id: uuid.UUID, status: str | None = None, session: Session = Depends(db)):
    stmt = select(DunningCase).where(DunningCase.tenant_id == tenant_id)
    if status:
        stmt = stmt.where(DunningCase.status == status)
    return [
        {"id": str(item.id), "billing_account_id": str(item.billing_account_id), "status": item.status, "current_stage_order": item.current_stage_order, "next_due_at": item.next_due_at}
        for item in session.scalars(stmt.order_by(DunningCase.created_at.desc()).limit(200))
    ]


@router.post("/dunning/promises", status_code=201)
def create_promise(payload: PromiseCreate, session: Session = Depends(db)):
    promise = record_promise_to_pay(session, payload.tenant_id, payload.billing_account_id, amount=payload.amount, currency=payload.currency, promise_date=payload.promise_date, created_by=payload.created_by)
    session.commit()
    return {"id": str(promise.id), "status": promise.status}


@router.post("/dunning/holds", status_code=201)
def create_hold(payload: HoldCreate, session: Session = Depends(db)):
    hold = place_collection_hold(session, payload.tenant_id, payload.billing_account_id, kind=payload.kind, reason=payload.reason, created_by=payload.created_by)
    session.commit()
    return {"id": str(hold.id), "kind": hold.kind, "status": hold.status}


# ===========================================================================
# Ledger / reports
# ===========================================================================

@router.get("/ledger/entries")
def ledger_entries(tenant_id: uuid.UUID, limit: int = 200, session: Session = Depends(db)):
    return [
        {"id": str(item.id), "entry_number": item.entry_number, "entry_type": item.entry_type, "currency": item.currency, "period_key": item.period_key, "effective_date": item.effective_date, "correlation_id": item.correlation_id, "description": item.description, "reversal_of": str(item.reversal_of) if item.reversal_of else None}
        for item in session.scalars(select(JournalEntry).where(JournalEntry.tenant_id == tenant_id).order_by(JournalEntry.created_at.desc()).limit(min(max(limit, 1), 500)))
    ]


@router.post("/ledger/rebuild-projection")
def rebuild_projection_endpoint(tenant_id: uuid.UUID, period_key: str, session: Session = Depends(db)):
    result = rebuild_projection(session, tenant_id, period_key)
    session.commit()
    return {"period_key": period_key, "balances": result}


@router.get("/ledger/balances")
def ledger_balances(tenant_id: uuid.UUID, period_key: str | None = None, session: Session = Depends(db)):
    return ledger_balances_report(session, tenant_id, period_key)


@router.get("/reports/daily-collections")
def report_daily_collections(tenant_id: uuid.UUID, session: Session = Depends(db)):
    return daily_collections(session, tenant_id)


@router.get("/reports/invoice-aging")
def report_invoice_aging(tenant_id: uuid.UUID, session: Session = Depends(db)):
    return invoice_aging(session, tenant_id)


@router.get("/reports/payment-methods")
def report_payment_methods(tenant_id: uuid.UUID, session: Session = Depends(db)):
    return payment_method_summary(session, tenant_id)


@router.get("/reports/refunds")
def report_refunds(tenant_id: uuid.UUID, session: Session = Depends(db)):
    return refund_summary(session, tenant_id)


@router.get("/reports/chargebacks")
def report_chargebacks(tenant_id: uuid.UUID, session: Session = Depends(db)):
    return chargeback_summary(session, tenant_id)


@router.get("/reports/settlements")
def report_settlements(tenant_id: uuid.UUID, session: Session = Depends(db)):
    return settlement_summary(session, tenant_id)


@router.get("/reports/reconciliation-exceptions")
def report_recon_exceptions(tenant_id: uuid.UUID, session: Session = Depends(db)):
    return recon_exception_summary(session, tenant_id)


@router.get("/reports/credit-balances")
def report_credit_balances(tenant_id: uuid.UUID, session: Session = Depends(db)):
    return credit_balance_report(session, tenant_id)


@router.get("/reports/outstanding")
def report_outstanding(tenant_id: uuid.UUID, session: Session = Depends(db)):
    return outstanding_report(session, tenant_id)
