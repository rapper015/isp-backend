"""BSS monetization & catalog tests (Master Spec Batch 5)."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app

AUTH = {"X-BSS-Service-Key": "test-internal-key"}


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _tid(tenant_id):
    return {"tenant_id": str(tenant_id)}


def test_bundle_products(client, tenant_id):
    r = client.post("/api/bss/catalog/bundles", json={
        "tenant_id": str(tenant_id), "bundle_code": "BND-BB-OTT",
        "name": "Broadband + OTT", "items": [{"code": "BB-100"}, {"code": "OTT-NETFLIX"}],
        "monthly_fee": "699.00"}, headers=AUTH)
    assert r.status_code == 201
    assert r.json()["status"] == "ACTIVE"
    assert len(r.json()["items"]) == 2


def test_service_catalog_and_sunset(client, tenant_id):
    client.post("/api/bss/catalog/services", json={
        "tenant_id": str(tenant_id), "code": "SVC-FIBER", "name": "Fiber Internet",
        "kind": "SERVICE", "logical_def": {"speed": "100 Mbps"}}, headers=AUTH)
    rl = client.get(f"/api/bss/catalog/services?tenant_id={tenant_id}", headers=AUTH)
    assert rl.json()[0]["kind"] == "SERVICE"
    s = client.post("/api/bss/catalog/products/SVC-FIBER/sunset", json=_tid(tenant_id), headers=AUTH)
    assert s.json()["status"] == "RETIRED"


def test_enterprise_catalog_and_vendor(client, tenant_id):
    e = client.post("/api/bss/catalog/enterprise", json={
        "tenant_id": str(tenant_id), "code": "ENT-LEASED", "name": "Leased Line",
        "vendor": "Tata", "terms": {"sla": "99.95%"}}, headers=AUTH)
    assert e.json()["vendor"] == "Tata"
    v = client.post("/api/bss/catalog/vendors", json={
        "tenant_id": str(tenant_id), "name": "Acme Networks", "sla_minutes": 240,
        "penalty_amount": "500.00"}, headers=AUTH)
    assert v.json()["sla_minutes"] == 240
    assert len(client.get(f"/api/bss/catalog/enterprise?tenant_id={tenant_id}", headers=AUTH).json()) == 1


def test_sla_pricing_and_penalties(client, tenant_id):
    client.post("/api/bss/catalog/sla-tiers", json={
        "tenant_id": str(tenant_id), "tier": "GOLD", "price_multiplier": 1.5,
        "penalty_pct": 10.0}, headers=AUTH)
    r = client.post("/api/bss/catalog/sla/price", json={
        "tenant_id": str(tenant_id), "base_price": "1000", "tier": "GOLD"}, headers=AUTH)
    assert str(r.json()["priced_amount"]) == "1500.00"
    assert float(r.json()["penalty_amount"]) == 150.0


def test_api_marketplace(client, tenant_id):
    r = client.post("/api/bss/catalog/api-marketplace", json={
        "tenant_id": str(tenant_id), "code": "GEO-API", "name": "Geo Location API",
        "price_per_call": "0.01", "tier": "PREMIUM"}, headers=AUTH)
    assert r.json()["status"] == "PUBLISHED"
    assert r.json()["price_per_call"] == "0.0100"


def test_commission_calculation(client, tenant_id):
    r = client.post("/api/bss/monetization/commissions/calculate", json={
        "tenant_id": str(tenant_id), "reseller_id": "res-1", "gross_sales": "10000",
        "rate": 10.0, "period": "MONTH"}, headers=AUTH)
    assert r.json()["commission_amount"] == "1000.00"
    assert r.json()["status"] == "CALCULATED"


def test_wallet_credit_deduct_balance(client, tenant_id):
    client.post("/api/bss/monetization/wallets/credit", json={
        "tenant_id": str(tenant_id), "wallet_id": "res-wallet-1", "amount": "500.00"}, headers=AUTH)
    d = client.post("/api/bss/monetization/wallets/deduct", json={
        "tenant_id": str(tenant_id), "wallet_id": "res-wallet-1", "amount": "200.00",
        "reason": "service"}, headers=AUTH)
    assert d.json()["balance"] == "300.00"
    bal = client.get(f"/api/bss/monetization/wallets/res-wallet-1/balance?tenant_id={tenant_id}", headers=AUTH)
    assert bal.json()["balance"] == "300.00"


def test_wallet_deduct_insufficient(client, tenant_id):
    r = client.post("/api/bss/monetization/wallets/deduct", json={
        "tenant_id": str(tenant_id), "wallet_id": "empty-1", "amount": "100.00"}, headers=AUTH)
    assert r.status_code == 400


def test_budget_planning(client, tenant_id):
    b = client.post("/api/bss/monetization/budgets", json={
        "tenant_id": str(tenant_id), "name": "CapEx FY26", "period": "YEAR",
        "limit_amount": "100000"}, headers=AUTH)
    bid = b.json()["id"]
    r = client.post(f"/api/bss/monetization/budgets/{bid}/spend", json={
        "tenant_id": str(tenant_id), "amount": "25000"}, headers=AUTH)
    assert r.json()["spent_amount"] == "25000.00"
    over = client.post(f"/api/bss/monetization/budgets/{bid}/spend", json={
        "tenant_id": str(tenant_id), "amount": "90000"}, headers=AUTH)
    assert over.status_code == 400  # BUDGET_EXCEEDED


def test_cost_and_profit_centers(client, tenant_id):
    c = client.post("/api/bss/finance/cost-centers", json={
        "tenant_id": str(tenant_id), "code": "CC-NETWORK", "name": "Network Ops",
        "budget": "50000"}, headers=AUTH)
    assert c.json()["code"] == "CC-NETWORK"
    p = client.post("/api/bss/finance/profit-centers", json={
        "tenant_id": str(tenant_id), "code": "PC-FIBER", "name": "Fiber", "target": "200000"},
        headers=AUTH)
    assert p.json()["name"] == "Fiber"


def test_feature_adoption_and_stickiness(client, tenant_id):
    client.post("/api/bss/analytics/feature-adoption", json={
        "tenant_id": str(tenant_id), "feature": "OTT", "subscriber_count": 100,
        "usage_count": 600}, headers=AUTH)
    s = client.post("/api/bss/analytics/stickiness", json={
        "tenant_id": str(tenant_id), "product": "OTT", "retention_pct": 80.0}, headers=AUTH)
    assert s.json()["stickiness_score"] > 0
    assert s.json()["product"] == "OTT"


def test_partner_sla_analytics(client, tenant_id):
    r = client.post("/api/bss/analytics/partner-sla", json={
        "tenant_id": str(tenant_id), "partner": "partner-a", "sla_pct": 98.5,
        "breaches": 2}, headers=AUTH)
    assert r.json()["breaches"] == 2
    assert r.json()["sla_pct"] == 98.5


def test_churn_lifecycle(client, tenant_id):
    c = client.post("/api/bss/analytics/churn/track", json={
        "tenant_id": str(tenant_id), "subscriber_id": "sub-1001", "stage": "AT_RISK",
        "reason": "low usage"}, headers=AUTH)
    assert c.json()["stage"] == "AT_RISK"
    c2 = client.post("/api/bss/analytics/churn/track", json={
        "tenant_id": str(tenant_id), "subscriber_id": "sub-1001", "stage": "CHURNED"},
        headers=AUTH)
    assert c2.json()["stage"] == "CHURNED"
    assert c2.json()["churned_at"] is not None


def test_trial_conversion_analytics(client, tenant_id):
    t = client.post("/api/bss/analytics/trials", json={
        "tenant_id": str(tenant_id), "subscriber_id": "sub-2001", "plan": "Fiber 200"},
        headers=AUTH)
    tid = t.json()["id"]
    conv = client.post(f"/api/bss/analytics/trials/{tid}/convert", json=_tid(tenant_id), headers=AUTH)
    assert conv.json()["converted"] is True
    rate = client.get(f"/api/bss/analytics/trials/conversion-rate?tenant_id={tenant_id}", headers=AUTH)
    assert rate.json()["trials"] == 1
    assert rate.json()["conversion_rate"] == 100.0


def test_requires_internal_key(client, tenant_id):
    r = client.post("/api/bss/catalog/services", json={
        "tenant_id": str(tenant_id), "code": "X", "name": "x"})
    assert r.status_code == 401


def test_tenant_isolation(client, tenant_id):
    other = uuid.uuid4()
    client.post("/api/bss/catalog/services", json={
        "tenant_id": str(tenant_id), "code": "SVC-A", "name": "A"}, headers=AUTH)
    rl = client.get(f"/api/bss/catalog/services?tenant_id={other}", headers=AUTH)
    assert rl.json() == []
