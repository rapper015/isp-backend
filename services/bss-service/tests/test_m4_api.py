"""M4 end-to-end API: invoice -> intent -> checkout -> capture -> receipt,
duplicate webhook dedup, overpayment credit, tenant isolation, reports."""
import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.revenue.gateways import sign_payload
from app.security import decrypt_secret

AUTH = {"X-BSS-Service-Key": "test-internal-key"}


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _new_tenant(client) -> str:
    r = client.post("/api/bss/tenants", json={"name": f"T-{uuid.uuid4().hex[:6]}", "code": f"T{uuid.uuid4().hex[:8].upper()}", "currency": "INR"}, headers=AUTH)
    return r.json()["id"]


def test_full_payment_flow_via_api(client, session, tenant, gateway):
    tenant_id = str(tenant.id)
    acct = client.post("/api/bss/billing-accounts", json={"tenant_id": tenant_id, "account_code": "ACC-1", "customer_ref": "cust-1"}, headers=AUTH).json()["id"]
    inv = client.post(
        "/api/bss/invoices",
        json={"tenant_id": tenant_id, "billing_account_id": acct, "invoice_number": "INV-API-1", "total_amount": 1000.0, "due_date": "2026-09-30T00:00:00Z", "lines": [{"description": "Fiber plan", "amount": 1000.0}]},
        headers=AUTH,
    )
    assert inv.status_code == 201

    intent = client.post(
        "/api/bss/payment-intents",
        json={"tenant_id": tenant_id, "billing_account_id": acct, "idempotency_key": f"it-{uuid.uuid4().hex}", "gateway_account_id": str(gateway.id), "invoice_ids": [inv.json()["id"]]},
        headers=AUTH,
    ).json()
    checkout = client.post(f"/api/bss/payment-intents/{intent['id']}/checkout", params={"tenant_id": tenant_id}, headers=AUTH)
    assert checkout.status_code == 200
    assert checkout.json()["status"] == "PENDING"

    cap = client.post(
        f"/api/bss/payment-intents/{intent['id']}/capture",
        json={"tenant_id": tenant_id, "intent_id": intent["id"], "external_ref": "api-capture-1", "amount": 1000.0, "currency": "INR", "mode": "test", "idempotency_key": f"cap-{uuid.uuid4().hex}"},
        headers=AUTH,
    )
    assert cap.status_code == 200
    txn_id = cap.json()["transaction_id"]

    receipt = client.get(f"/api/bss/payments/{txn_id}/receipt", params={"tenant_id": tenant_id}, headers=AUTH)
    assert receipt.status_code == 200
    assert receipt.json()["amount"] == "1000.00"

    outstanding = client.get(f"/api/bss/billing-accounts/{acct}/outstanding", params={"tenant_id": tenant_id}, headers=AUTH)
    assert outstanding.json()["payable"] == "0.00"

    # Reports
    assert client.get("/api/bss/reports/daily-collections", params={"tenant_id": tenant_id}, headers=AUTH).status_code == 200
    assert client.get("/api/bss/reports/invoice-aging", params={"tenant_id": tenant_id}, headers=AUTH).status_code == 200
    assert client.get("/api/bss/reports/payment-methods", params={"tenant_id": tenant_id}, headers=AUTH).status_code == 200


def test_webhook_endpoint_and_dedup(client, session, tenant, gateway):
    tenant_id = str(tenant.id)
    acct = client.post("/api/bss/billing-accounts", json={"tenant_id": tenant_id, "account_code": "ACC-2", "customer_ref": "cust-2"}, headers=AUTH).json()["id"]
    inv = client.post(
        "/api/bss/invoices",
        json={"tenant_id": tenant_id, "billing_account_id": acct, "invoice_number": "INV-WH-1", "total_amount": 500.0, "due_date": "2026-09-30T00:00:00Z", "lines": [{"description": "Plan", "amount": 500.0}]},
        headers=AUTH,
    ).json()["id"]
    intent = client.post("/api/bss/payment-intents", json={"tenant_id": tenant_id, "billing_account_id": acct, "idempotency_key": f"wh-{uuid.uuid4().hex}", "invoice_ids": [inv]}, headers=AUTH).json()
    body = json.dumps({"payment_intent_id": intent["id"], "external_ref": "wh-capture-1", "amount": 500, "currency": "INR"})
    secret = decrypt_secret(gateway.webhook_secret_ciphertext)
    signature = sign_payload(secret, body)
    headers = {**AUTH, "X-Razorpay-Signature": signature, "X-Event-Id": "evt-api-1", "X-Event-Type": "payment.captured.v1"}
    r1 = client.post(f"/api/bss/webhooks/gateway/{gateway.id}?tenant_id={tenant_id}", content=body, headers=headers)
    assert r1.status_code == 200
    r2 = client.post(f"/api/bss/webhooks/gateway/{gateway.id}?tenant_id={tenant_id}", content=body, headers=headers)
    assert r2.status_code == 200
    payments = client.get("/api/bss/payments", params={"tenant_id": tenant_id}, headers=AUTH)
    assert len(payments.json()) == 1  # duplicate webhook -> one payment
    assert client.get("/api/bss/invoices/{}/".format(inv), params={"tenant_id": tenant_id}, headers=AUTH).json()["status"] == "PAID"


def test_invalid_webhook_signature_via_api(client, tenant, gateway):
    body = json.dumps({"payment_intent_id": str(uuid.uuid4()), "amount": 100})
    r = client.post(f"/api/bss/webhooks/gateway/{gateway.id}?tenant_id={tenant.id}", content=body, headers={**AUTH, "X-Razorpay-Signature": "nope", "X-Event-Id": "evt-bad", "X-Event-Type": "payment.captured.v1"})
    assert r.status_code == 400
    history = client.get("/api/bss/webhooks", params={"tenant_id": str(tenant.id)}, headers=AUTH)
    assert history.json()[0]["signature_valid"] is False


def test_overpayment_credit_and_refund_api(client, session, tenant, gateway):
    tenant_id = str(tenant.id)
    acct = client.post("/api/bss/billing-accounts", json={"tenant_id": tenant_id, "account_code": "ACC-3", "customer_ref": "cust-3"}, headers=AUTH).json()["id"]
    inv = client.post(
        "/api/bss/invoices",
        json={"tenant_id": tenant_id, "billing_account_id": acct, "invoice_number": "INV-OV-1", "total_amount": 800.0, "due_date": "2026-09-30T00:00:00Z", "lines": [{"description": "Plan", "amount": 800.0}]},
        headers=AUTH,
    ).json()["id"]
    intent = client.post(
        "/api/bss/payment-intents",
        json={"tenant_id": tenant_id, "billing_account_id": acct, "amount": 1000.0, "allow_overpayment": True, "idempotency_key": f"ov-{uuid.uuid4().hex}", "invoice_ids": [inv]},
        headers=AUTH,
    ).json()
    txn = client.post(
        f"/api/bss/payment-intents/{intent['id']}/capture",
        json={"tenant_id": tenant_id, "intent_id": intent["id"], "external_ref": "ov-capture-1", "amount": 1000.0, "currency": "INR", "mode": "test", "idempotency_key": f"cap-{uuid.uuid4().hex}"},
        headers=AUTH,
    ).json()
    balances = client.get("/api/bss/reports/credit-balances", params={"tenant_id": tenant_id}, headers=AUTH)
    assert any(item["credit_balance"] == "200.00" for item in balances.json())

    refund = client.post(
        "/api/bss/refunds",
        json={"tenant_id": tenant_id, "transaction_id": txn["transaction_id"], "amount": 300.0, "currency": "INR", "refund_reference": "RFD-API-1", "requires_approval": False, "approved_by": "tester"},
        headers=AUTH,
    )
    assert refund.status_code == 201
    assert refund.json()["status"] == "COMPLETED"


def test_manual_payment_approval_flow_api(client, session, tenant):
    tenant_id = str(tenant.id)
    acct = client.post("/api/bss/billing-accounts", json={"tenant_id": tenant_id, "account_code": "ACC-4", "customer_ref": "cust-4"}, headers=AUTH).json()["id"]
    client.post(
        "/api/bss/invoices",
        json={"tenant_id": tenant_id, "billing_account_id": acct, "invoice_number": "INV-MP-1", "total_amount": 50000.0, "due_date": "2026-09-30T00:00:00Z", "lines": [{"description": "Plan", "amount": 50000.0}]},
        headers=AUTH,
    )
    mp = client.post(
        "/api/bss/manual-payments",
        json={"tenant_id": tenant_id, "billing_account_id": acct, "reference_number": "MP-API-1", "method": "NEFT", "amount": 50000.0, "currency": "INR", "external_reference": "UTR-API-1", "collector": "branch"},
        headers=AUTH,
    ).json()
    assert mp["requires_approval"] is True
    client.post(f"/api/bss/manual-payments/{mp['id']}/submit", json={"tenant_id": tenant_id, "actor": "operator"}, headers=AUTH)
    rejected = client.post(f"/api/bss/manual-payments/{mp['id']}/approve", json={"tenant_id": tenant_id, "actor": "finance"}, headers=AUTH)
    assert rejected.status_code == 200
    posted = client.post(f"/api/bss/manual-payments/{mp['id']}/post", json={"tenant_id": tenant_id, "actor": "finance"}, headers=AUTH)
    assert posted.status_code == 200


def test_tenant_isolation(client, session):
    t1 = _new_tenant(client)
    t2 = _new_tenant(client)
    a1 = client.post("/api/bss/billing-accounts", json={"tenant_id": t1, "account_code": "ACC-T1", "customer_ref": "c"}, headers=AUTH).json()["id"]
    a2 = client.post("/api/bss/billing-accounts", json={"tenant_id": t2, "account_code": "ACC-T2", "customer_ref": "c"}, headers=AUTH).json()["id"]
    # Tenant 1 cannot see tenant 2's account.
    accounts_t1 = client.get("/api/bss/billing-accounts", params={"tenant_id": t1}, headers=AUTH).json()
    assert all(item["id"] != a2 for item in accounts_t1)
    assert any(item["id"] == a1 for item in accounts_t1)
    # Tenant 1 cannot fetch tenant 2's invoice via a forged reference.
    inv = client.post("/api/bss/invoices", json={"tenant_id": t2, "billing_account_id": a2, "invoice_number": "INV-T2", "total_amount": 10.0, "due_date": "2026-09-30T00:00:00Z", "lines": []}, headers=AUTH).json()
    assert client.get(f"/api/bss/invoices/{inv['id']}", params={"tenant_id": t1}, headers=AUTH).status_code == 404
