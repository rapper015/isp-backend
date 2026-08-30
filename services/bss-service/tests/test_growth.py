"""BSS growth & engagement tests (Batch 8: 682, 690, 808, 903, 1265, 1497)."""
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


def test_coupon_engine(client, tenant_id):
    c = client.post("/api/bss/growth/coupons", headers=AUTH, json={
        **_tid(tenant_id), "code": "WELCOME10", "discount_type": "PERCENT",
        "discount_value": "10.00", "max_uses": 5})
    assert c.status_code == 201
    applied = client.post("/api/bss/growth/coupons/apply", headers=AUTH, json={
        **_tid(tenant_id), "code": "WELCOME10", "amount": "1000.00"})
    assert applied.json()["discount"] == "100.00"
    assert applied.json()["payable"] == "900.00"


def test_redemption(client, tenant_id):
    r = client.post("/api/bss/growth/redemptions", headers=AUTH, json={
        **_tid(tenant_id), "customer_id": "CUST-1", "points": 500,
        "reward": "500MB data add-on"})
    assert r.status_code == 201
    assert r.json()["status"] == "REDEEMED"
    assert r.json()["points"] == 500


def test_dynamic_service_composition(client, tenant_id):
    c = client.post("/api/bss/growth/compositions", headers=AUTH, json={
        **_tid(tenant_id), "composition_code": "COMP-HOME-PRO",
        "components": [{"code": "FTTH-300"}, {"code": "STB-4K"}], "price": "1299.00"})
    assert c.status_code == 201
    assert c.json()["components"] == 2
    assert c.json()["price"] == "1299.00"


def test_expense_intelligence(client, tenant_id):
    e = client.post("/api/bss/growth/expenses/categorize", headers=AUTH, json={
        **_tid(tenant_id), "description": "Office rent for Q3", "amount": "45000.00"})
    assert e.status_code == 201
    assert e.json()["category"] == "FACILITY"
    assert e.json()["confidence"] == 0.85


def test_margin_optimization(client, tenant_id):
    m = client.post("/api/bss/growth/margin/optimize", headers=AUTH, json={
        **_tid(tenant_id), "segment": "FTTH", "period": "MONTH", "current_margin_pct": 32.0})
    assert m.status_code == 201
    assert m.json()["optimized_margin_pct"] > 32.0
    assert m.json()["recommendation"]


def test_viral_growth_referral(client, tenant_id):
    r = client.post("/api/bss/growth/referrals", headers=AUTH, json={
        **_tid(tenant_id), "referrer_id": "CUST-1", "referee_id": "CUST-2",
        "reward": "200.00"})
    assert r.status_code == 201
    assert r.json()["status"] == "PENDING"
    assert r.json()["reward"] == "200.00"


def test_coupon_requires_tenant(client):
    r = client.post("/api/bss/growth/coupons", headers=AUTH, json={
        "code": "NO-TENANT", "discount_type": "FIXED", "discount_value": "10"})
    assert r.status_code == 422


def test_coupon_requires_auth(client, tenant_id):
    assert client.post("/api/bss/growth/coupons", json=_tid(tenant_id)).status_code == 401
