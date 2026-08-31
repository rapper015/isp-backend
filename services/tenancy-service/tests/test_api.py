"""HTTP API flows for the tenancy service."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from conftest import make_token


@pytest.fixture
def client(defaults):
    with TestClient(app) as c:
        yield c


def _headers(role, tenant_id=None):
    return {"Authorization": f"Bearer {make_token(role, tenant_id)}"}


def test_health_and_status(client):
    assert client.get("/health").status_code == 200
    assert client.get("/status").json()["service"] == "tenancy"


def test_tenant_provision_via_api(client, platform_headers):
    r = client.post("/api/tenancy/tenants", json={
        "name": "API ISP", "code": "APIISP1", "currency": "INR"}, headers=platform_headers)
    assert r.status_code == 201, r.text
    tenant_id = r.json()["id"]
    r = client.post(f"/api/tenancy/tenants/{tenant_id}/provision", headers=platform_headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ACTIVE"


def test_tenant_suspend_requires_elevated(client, tenant, platform_headers):
    headers = _headers("AUDITOR", tenant.id)
    r = client.post(f"/api/tenancy/tenants/{tenant.id}/suspend",
                    json={"reason": "test", "scope": "ADMIN_CONSOLE"}, headers=headers)
    assert r.status_code == 403  # AUDITOR lacks elevated tenants.suspend
    r = client.post(f"/api/tenancy/tenants/{tenant.id}/suspend",
                    json={"reason": "overdue", "scope": "BILLING"}, headers=platform_headers)
    assert r.status_code == 200
    assert r.json()["status"] == "SUSPENDED"


def test_tenant_config_and_domain_via_api(client, tenant, auth_headers):
    r = client.put(f"/api/tenancy/tenants/{tenant.id}/config",
                   json={"category": "portal", "config": {"branding": {"theme": {"primary": "#123"}}}},
                   headers=auth_headers)
    assert r.status_code == 200
    r = client.post(f"/api/tenancy/tenants/{tenant.id}/domains",
                    json={"domain": "portal.example.com"}, headers=auth_headers)
    assert r.status_code == 201
    token = r.json()["verification_token"]
    domain_id = r.json()["id"]
    r = client.post(f"/api/tenancy/tenants/{tenant.id}/domains/{domain_id}/verify",
                    json={"token": token}, headers=auth_headers)
    assert r.status_code == 200 and r.json()["status"] == "ACTIVE"


def test_partner_and_ownership_via_api(client, tenant, auth_headers):
    r = client.post(f"/api/tenancy/tenants/{tenant.id}/partners",
                    json={"partner_type": "FRANCHISE", "code": "FR-API", "name": "Franchise"},
                    headers=auth_headers)
    assert r.status_code == 201, r.text
    partner_id = r.json()["id"]
    r = client.post(f"/api/tenancy/tenants/{tenant.id}/partners/{partner_id}/status",
                    json={"to_status": "ONBOARDING", "reason": "onboard"}, headers=auth_headers)
    assert r.status_code == 200
    r = client.post(f"/api/tenancy/tenants/{tenant.id}/partners/{partner_id}/status",
                    json={"to_status": "ACTIVE", "reason": "onboarded"}, headers=auth_headers)
    assert r.status_code == 200 and r.json()["status"] == "ACTIVE"
    r = client.post(f"/api/tenancy/tenants/{tenant.id}/ownership",
                    json={"customer_id": "C-API", "acquisition_partner_id": partner_id},
                    headers=auth_headers)
    assert r.status_code == 200


def test_commission_flow_via_api(client, tenant, auth_headers, make_partner):
    partner = make_partner()
    r = client.post(f"/api/tenancy/tenants/{tenant.id}/commission-plans",
                    json={"code": "PLAN-API", "name": "Plan"}, headers=auth_headers)
    assert r.status_code == 201, r.text
    plan_id = r.json()["id"]
    r = client.post(f"/api/tenancy/tenants/{tenant.id}/commission-plans/{plan_id}/rules",
                    json={"code": "R-API", "name": "rule", "basis": "PAYMENT_COLLECTION",
                          "calculation_type": "PERCENTAGE", "rate": 5}, headers=auth_headers)
    assert r.status_code == 201
    r = client.post(f"/api/tenancy/tenants/{tenant.id}/commission-plans/{plan_id}/approve",
                    headers=auth_headers)
    assert r.status_code == 200
    r = client.post(f"/api/tenancy/tenants/{tenant.id}/commission-agreements",
                    json={"partner_id": str(partner.id), "plan_id": plan_id}, headers=auth_headers)
    assert r.status_code == 201
    r = client.post(f"/api/tenancy/tenants/{tenant.id}/commission-earnings",
                    json={"partner_id": str(partner.id), "source_event_id": "evt-api-1",
                          "source_event_type": "billing.payment.captured.v1",
                          "basis": "PAYMENT_COLLECTION", "basis_amount": 1000}, headers=auth_headers)
    assert r.status_code == 201, r.text
    assert r.json()["amount"] == 50.0


def test_settlement_flow_via_api(client, tenant, auth_headers, make_partner, make_commission_plan):
    from app.database import SessionLocal
    from app.services import commission_service

    partner = make_partner()
    plan, rule = make_commission_plan()
    s = SessionLocal()
    commission_service.create_agreement(s, tenant.id, partner_id=partner.id, plan_id=plan.id)
    commission_service.recognize_earning(s, tenant.id, partner_id=partner.id,
                                         source_event_id="evt-st-1",
                                         source_event_type="billing.payment.captured.v1",
                                         basis="PAYMENT_COLLECTION", basis_amount=1000)
    s.commit()
    s.close()
    r = client.post(f"/api/tenancy/tenants/{tenant.id}/settlement-cycles",
                    json={"code": "CYC-API", "period_start": "2026-08-01", "period_end": "2026-08-31"},
                    headers=auth_headers)
    assert r.status_code == 201, r.text
    cycle_id = r.json()["id"]
    r = client.post(f"/api/tenancy/tenants/{tenant.id}/settlements",
                    json={"partner_id": str(partner.id), "cycle_id": cycle_id}, headers=auth_headers)
    assert r.status_code == 201, r.text
    settlement_id = r.json()["id"]
    r = client.post(f"/api/tenancy/tenants/{tenant.id}/settlements/{settlement_id}/calculate",
                    headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["net"] == 100.0
    r = client.post(f"/api/tenancy/tenants/{tenant.id}/settlements/{settlement_id}/review",
                    headers=auth_headers)
    assert r.status_code == 200
    r = client.post(f"/api/tenancy/tenants/{tenant.id}/settlements/{settlement_id}/approve",
                    headers=auth_headers)
    assert r.status_code == 200
    r = client.post(f"/api/tenancy/tenants/{tenant.id}/settlements/{settlement_id}/lock",
                    headers=auth_headers)
    assert r.status_code == 200
    r = client.post(f"/api/tenancy/tenants/{tenant.id}/settlements/{settlement_id}/statement",
                    headers=auth_headers)
    assert r.status_code == 200 and r.json()["data"]["net"] == 100.0


def test_platform_aggregate_requires_platform_scope(client, tenant, auth_headers, platform_headers):
    r = client.get("/api/tenancy/reports/aggregate", params={"metric": "commission"}, headers=auth_headers)
    assert r.status_code == 403  # tenant admin cannot see aggregate
    r = client.get("/api/tenancy/reports/aggregate", params={"metric": "commission"},
                   headers=platform_headers)
    assert r.status_code == 200
