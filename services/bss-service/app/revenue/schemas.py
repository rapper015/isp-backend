"""Pydantic schemas for BSS revenue endpoints."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class TenantCreate(BaseModel):
    name: str
    code: str
    currency: str = "INR"


class BillingAccountCreate(BaseModel):
    tenant_id: uuid.UUID
    account_code: str
    customer_ref: str
    currency: str = "INR"


class InvoiceCreate(BaseModel):
    tenant_id: uuid.UUID
    billing_account_id: uuid.UUID
    invoice_number: str
    currency: str = "INR"
    total_amount: Decimal = Field(gt=0)
    due_date: datetime
    plan_reference: str | None = None
    lines: list[dict] = Field(default_factory=list)
    external_reference: str | None = None


class IntentCreate(BaseModel):
    tenant_id: uuid.UUID
    billing_account_id: uuid.UUID
    amount: Decimal | None = None
    currency: str = "INR"
    invoice_ids: list[uuid.UUID] = Field(default_factory=list)
    description: str | None = None
    idempotency_key: str
    gateway_account_id: uuid.UUID | None = None
    allow_overpayment: bool = False
    created_by: str = "system"


class CaptureRequest(BaseModel):
    tenant_id: uuid.UUID
    intent_id: uuid.UUID
    external_ref: str
    amount: Decimal
    currency: str
    method: str | None = None
    mode: str = "test"
    idempotency_key: str
    gateway_account_id: uuid.UUID | None = None


class GatewayAccountCreate(BaseModel):
    tenant_id: uuid.UUID
    code: str
    gateway_code: str
    mode: str = "test"
    api_key: str
    secret: str
    webhook_secret: str
    currency: str = "INR"
    methods: list[str] = Field(default_factory=list)
    is_default: bool = False
    priority: int = 100


class RefundCreate(BaseModel):
    tenant_id: uuid.UUID
    transaction_id: uuid.UUID
    amount: Decimal
    currency: str
    reason: str | None = None
    refund_reference: str
    requires_approval: bool = False
    approved_by: str | None = None


class ManualPaymentCreate(BaseModel):
    tenant_id: uuid.UUID
    billing_account_id: uuid.UUID
    reference_number: str
    method: str
    amount: Decimal
    currency: str = "INR"
    external_reference: str | None = None
    payment_date: datetime | None = None
    collector: str | None = None
    branch_reference: str | None = None
    evidence: dict = Field(default_factory=dict)
    notes: str | None = None
    correlation_id: str | None = None


class ManualPaymentAction(BaseModel):
    tenant_id: uuid.UUID
    actor: str = "operator"
    reason: str | None = None


class ReconImport(BaseModel):
    tenant_id: uuid.UUID
    kind: str = "TRANSACTION"
    items: list[dict] = Field(default_factory=list)
    import_source: str = "api"


class SettlementImport(BaseModel):
    tenant_id: uuid.UUID
    settlement_reference: str
    net_amount: Decimal
    currency: str
    fee_amount: Decimal = Decimal("0.00")
    settlement_date: datetime | None = None
    bank_reference: str | None = None
    gateway_account_id: uuid.UUID | None = None
    lines: list[dict] = Field(default_factory=list)


class ReconResolve(BaseModel):
    tenant_id: uuid.UUID
    notes: str
    resolved_by: str


class DunningPolicyCreate(BaseModel):
    tenant_id: uuid.UUID
    code: str
    name: str
    params: dict = Field(default_factory=dict)


class DunningStageCreate(BaseModel):
    tenant_id: uuid.UUID
    policy_version_id: uuid.UUID
    stage_order: int
    stage_code: str
    delay_seconds: int = 0
    action_type: str = "NOTIFY"
    message_template: str | None = None


class DunningCaseAction(BaseModel):
    tenant_id: uuid.UUID
    actor: str = "system"
    correlation_id: str | None = None


class PromiseCreate(BaseModel):
    tenant_id: uuid.UUID
    billing_account_id: uuid.UUID
    amount: Decimal
    currency: str = "INR"
    promise_date: datetime
    created_by: str = "operator"


class HoldCreate(BaseModel):
    tenant_id: uuid.UUID
    billing_account_id: uuid.UUID
    kind: str
    reason: str | None = None
    created_by: str = "operator"


class ChargebackCreate(BaseModel):
    tenant_id: uuid.UUID
    transaction_id: uuid.UUID
    gateway_dispute_ref: str
    amount: Decimal
    currency: str
    reason: str | None = None
    evidence_deadline: datetime | None = None


class WebhookAck(BaseModel):
    tenant_id: uuid.UUID
    gateway_account_id: uuid.UUID
    signature: str
    external_event_id: str
    event_type: str
