"""M4 webhook security: signature verification, dedup, redaction."""
import json
import uuid

import pytest

from app.revenue.gateways import sign_payload
from app.revenue.models import GatewayWebhook, PaymentTransaction
from app.revenue.webhooks import receive_webhook
from app.security import decrypt_secret
from app.revenue.payments import capture_payment
from app.revenue.models import PaymentIntent, RevenueInvoice
from decimal import Decimal


def _signed_body(gateway, payload: dict) -> tuple[str, str]:
    body = json.dumps(payload)
    secret = decrypt_secret(gateway.webhook_secret_ciphertext)
    return body, sign_payload(secret, body)


def test_invalid_signature_rejected_and_stored(session, tenant, gateway):
    body = json.dumps({"payment_intent_id": str(uuid.uuid4()), "amount": 100, "currency": "INR"})
    with pytest.raises(ValueError):
        receive_webhook(
            session,
            tenant.id,
            gateway_account_id=gateway.id,
            raw_body=body,
            signature="bad-signature",
            external_event_id="evt-invalid",
            event_type="payment.captured.v1",
            correlation_id="c1",
        )
    session.commit()
    webhook = session.query(GatewayWebhook).filter(GatewayWebhook.tenant_id == tenant.id, GatewayWebhook.external_event_id == "evt-invalid").one()
    assert webhook.signature_valid is False
    assert webhook.status == "INVALID_SIGNATURE"


def test_duplicate_webhook_does_not_duplicate_payment(session, tenant, account, gateway, invoice):
    intent = __import__("app.revenue.payments", fromlist=["create_payment_intent"]).create_payment_intent(
        session, tenant.id, billing_account_id=account.id, idempotency_key=f"wh-{uuid.uuid4().hex}", correlation_id="c1", invoice_ids=[invoice.id]
    )
    session.commit()
    payload = {"payment_intent_id": str(intent.id), "external_ref": "wh-ext-1", "amount": 1000, "currency": "INR", "method": "UPI"}
    body, signature = _signed_body(gateway, payload)
    w1 = receive_webhook(session, tenant.id, gateway_account_id=gateway.id, raw_body=body, signature=signature, external_event_id="evt-dup-1", event_type="payment.captured.v1", correlation_id="c1")
    session.commit()
    w2 = receive_webhook(session, tenant.id, gateway_account_id=gateway.id, raw_body=body, signature=signature, external_event_id="evt-dup-1", event_type="payment.captured.v1", correlation_id="c1")
    session.commit()
    assert w1.id == w2.id
    assert session.query(PaymentTransaction).filter(PaymentTransaction.tenant_id == tenant.id).count() == 1  # one financial posting
    assert session.get(RevenueInvoice, invoice.id).status == "PAID"


def test_valid_capture_webhook_posts_payment(session, tenant, account, gateway, invoice):
    intent = __import__("app.revenue.payments", fromlist=["create_payment_intent"]).create_payment_intent(
        session, tenant.id, billing_account_id=account.id, idempotency_key=f"wh-{uuid.uuid4().hex}", correlation_id="c1", invoice_ids=[invoice.id]
    )
    session.commit()
    payload = {"payment_intent_id": str(intent.id), "external_ref": "wh-ext-2", "amount": 1000, "currency": "INR"}
    body, signature = _signed_body(gateway, payload)
    webhook = receive_webhook(session, tenant.id, gateway_account_id=gateway.id, raw_body=body, signature=signature, external_event_id="evt-ok-2", event_type="payment.captured.v1", correlation_id="c1")
    session.commit()
    assert webhook.status == "PROCESSED"
    txn = session.query(PaymentTransaction).filter(PaymentTransaction.tenant_id == tenant.id).one()
    assert txn.external_ref == "wh-ext-2"
    assert txn.amount == Decimal("1000.00")


def test_webhook_payload_is_redacted(session, tenant, gateway):
    body, signature = _signed_body(gateway, {"event": "payment.failed", "payload": {"payment_intent_id": str(uuid.uuid4()), "amount": 100, "card": {"pan": "4111111111111111", "cvv": "123"}, "upi_pin": "0000"}})
    webhook = receive_webhook(session, tenant.id, gateway_account_id=gateway.id, raw_body=body, signature=signature, external_event_id="evt-redact", event_type="payment.failed.v1", correlation_id="c1")
    session.commit()
    assert "4111111111111111" not in json.dumps(webhook.redacted_payload)
    assert "123" not in json.dumps(webhook.redacted_payload)
    assert "0000" not in json.dumps(webhook.redacted_payload)
