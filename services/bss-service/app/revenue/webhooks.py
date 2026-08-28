"""Secure webhook processing.

Raw body is preserved for signature verification; signatures are verified BEFORE
parsing. Gateway event IDs are deduplicated with a unique constraint. Sensitive
payload fields are redacted before storage."""
from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from .gateways import get_gateway_class
from .models import GatewayAccount, GatewayWebhook
from .payments import capture_payment
from ..security import decrypt_secret

SENSITIVE_PAYLOAD_KEYS = ("card", "pan", "cvv", "otp", "token", "upi_pin", "password", "netbanking_password", "key", "secret", "signature", "sign")


def _redact(value, depth: int = 0):
    if depth > 6:
        return "[REDACTED]"
    if isinstance(value, dict):
        return {key: ("[REDACTED]" if str(key).lower() in SENSITIVE_PAYLOAD_KEYS else _redact(item, depth + 1)) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item, depth + 1) for item in value]
    return value


def redact_payload(payload) -> dict:
    return _redact(payload)


def receive_webhook(
    session: Session,
    tenant_id,
    *,
    gateway_account_id,
    raw_body: str,
    signature: str,
    external_event_id: str,
    event_type: str,
    correlation_id: str,
) -> GatewayWebhook:
    """Verify, dedupe, store and dispatch a gateway webhook. Returns the stored
    record; raises ValueError for invalid signatures / unknown events."""
    gateway_account = session.scalar(select(GatewayAccount).where(GatewayAccount.id == gateway_account_id, GatewayAccount.tenant_id == tenant_id))
    if gateway_account is None:
        raise ValueError("gateway account not found")
    adapter = get_gateway_class(gateway_account.gateway_code)()
    secret = decrypt_secret(gateway_account.webhook_secret_ciphertext)
    raw_hash = hashlib.sha256(raw_body.encode()).hexdigest()
    existing = session.scalar(select(GatewayWebhook).where(GatewayWebhook.tenant_id == tenant_id, GatewayWebhook.external_event_id == external_event_id))
    if existing is not None:
        return existing  # deduplicated; never reprocesses the financial effect

    signature_valid = adapter.verify_webhook(raw_body, signature, secret)
    if not signature_valid:
        webhook = GatewayWebhook(
            tenant_id=tenant_id,
            gateway_account_id=gateway_account.id,
            external_event_id=external_event_id,
            event_type=event_type,
            signature_valid=False,
            raw_hash=raw_hash,
            redacted_payload={},
            status="INVALID_SIGNATURE",
            correlation_id=correlation_id,
        )
        session.add(webhook)
        session.flush()
        raise ValueError("invalid webhook signature")

    parsed = adapter.parse_webhook(raw_body)
    payload = parsed.get("payload", parsed)
    webhook = GatewayWebhook(
        tenant_id=tenant_id,
        gateway_account_id=gateway_account.id,
        external_event_id=external_event_id,
        event_type=event_type,
        signature_valid=True,
        raw_hash=raw_hash,
        redacted_payload=redact_payload(payload),
        status="RECEIVED",
        correlation_id=correlation_id,
    )
    session.add(webhook)
    session.flush()
    _dispatch(session, tenant_id, gateway_account, webhook, payload)
    return webhook


def _dispatch(session: Session, tenant_id, gateway_account: GatewayAccount, webhook: GatewayWebhook, payload: dict) -> None:
    """Dispatch a verified webhook to business processing. Processing is
    idempotent (unique idempotency_key on the transaction)."""
    event_type = webhook.event_type.lower()
    intent_id = payload.get("payment_intent_id")
    external_ref = payload.get("external_ref") or payload.get("payment_id") or webhook.external_event_id
    amount = payload.get("amount")
    currency = payload.get("currency", gateway_account.currency)
    if "captured" in event_type or "payment.captured" in event_type:
        if not intent_id or amount is None:
            raise ValueError("captured webhook missing payment_intent_id or amount")
        from .money import money

        capture_payment(
            session,
            tenant_id,
            intent_id=__import__("uuid").UUID(str(intent_id)),
            external_ref=str(external_ref),
            amount=money(amount),
            currency=currency,
            method=payload.get("method"),
            mode=gateway_account.mode,
            idempotency_key=webhook.external_event_id,
            correlation_id=webhook.correlation_id,
            gateway_account_id=gateway_account.id,
        )
        webhook.status = "PROCESSED"
        webhook.processed_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    elif "failed" in event_type:
        webhook.status = "PROCESSED"
        webhook.processed_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    elif "refund" in event_type:
        from .refunds import complete_refund_from_webhook

        complete_refund_from_webhook(session, tenant_id, webhook, payload)
        webhook.status = "PROCESSED"
        webhook.processed_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    else:
        webhook.status = "UNHANDLED"


def list_webhooks(session: Session, tenant_id, status: str | None = None, limit: int = 100) -> list[GatewayWebhook]:
    stmt = select(GatewayWebhook).where(GatewayWebhook.tenant_id == tenant_id)
    if status:
        stmt = stmt.where(GatewayWebhook.status == status)
    return list(session.scalars(stmt.order_by(GatewayWebhook.received_at.desc()).limit(min(max(limit, 1), 500))))
