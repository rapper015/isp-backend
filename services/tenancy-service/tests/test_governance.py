"""Tenancy governance tests (Master Spec Batch 4: messaging, campaigns,
usage/cost, policy/compliance, threat hunting, chains, insights, search,
procurement, ROI, scaling, mesh/mTLS, cloud, translations)."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from conftest import make_token


@pytest.fixture
def client(defaults):
    with TestClient(app) as c:
        yield c


def _notif(client, headers, **over):
    body = {"recipient": "user@isp.test", "channel": "EMAIL", "template": "welcome"}
    body.update(over)
    return client.post("/api/tenancy/governance/notifications", json=body, headers=headers)


def test_notification_retry_and_delivery(client, auth_headers):
    nid = _notif(client, auth_headers).json()["id"]
    r = client.post(f"/api/tenancy/governance/notifications/{nid}/deliver", headers=auth_headers)
    assert r.json()["status"] == "SENT"
    assert r.json()["delivered_at"] is not None
    d = client.get(f"/api/tenancy/governance/notifications/delivery/{nid}", headers=auth_headers)
    assert d.json()["status"] == "SENT"


def test_notification_retry_max_attempts(client, auth_headers):
    nid = _notif(client, auth_headers).json()["id"]
    # default max_attempts=5, each retry increments
    for _ in range(5):
        client.post(f"/api/tenancy/governance/notifications/{nid}/retry", headers=auth_headers)
    r = client.post(f"/api/tenancy/governance/notifications/{nid}/retry", headers=auth_headers)
    assert r.status_code == 400


def test_campaign_lifecycle_and_analytics(client, auth_headers):
    cid = client.post("/api/tenancy/governance/campaigns", headers=auth_headers, json={
        "name": "Diwali promo", "channel": "SMS", "audience": ["a@x.com", "b@x.com", "c@x.com"]}
    ).json()["id"]
    r = client.post(f"/api/tenancy/governance/campaigns/{cid}/schedule", headers=auth_headers,
                    json={"schedule_at": "2026-09-05T10:00:00+00:00"})
    assert r.json()["status"] == "SCHEDULED"
    e = client.post(f"/api/tenancy/governance/campaigns/{cid}/execute", headers=auth_headers)
    assert e.json()["status"] == "RUNNING"
    assert e.json()["audience_size"] == 3
    client.post(f"/api/tenancy/governance/campaigns/{cid}/track", headers=auth_headers,
                json={"recipient": "a@x.com", "event": "OPENED"})
    client.post(f"/api/tenancy/governance/campaigns/{cid}/track", headers=auth_headers,
                json={"recipient": "a@x.com", "event": "CONVERTED"})
    a = client.get(f"/api/tenancy/governance/campaigns/{cid}/analytics", headers=auth_headers).json()
    assert a["sent"] == 3
    assert a["opened"] == 1
    assert a["converted"] == 1
    assert a["conversion_rate"] > 0


def test_usage_meter_and_cost_optimization(client, auth_headers):
    client.post("/api/tenancy/governance/usage-meter", headers=auth_headers,
                json={"resource": "CPU", "amount": 120.5, "unit": "HOURS"})
    r = client.get("/api/tenancy/governance/usage-meter", headers=auth_headers)
    assert r.json()[0]["resource"] == "CPU"
    client.post("/api/tenancy/governance/costs", headers=auth_headers,
                json={"category": "STORAGE", "amount": 800, "storage_class": "STANDARD", "volume_gb": 900})
    opt = client.post("/api/tenancy/governance/costs/optimize", headers=auth_headers)
    assert opt.json()["cold_tier_candidates"] == 1


def test_policy_evaluate_and_compliance(client, auth_headers):
    pid = client.post("/api/tenancy/governance/policies", headers=auth_headers, json={
        "name": "CPU cap", "category": "RESOURCE",
        "rule_json": {"field": "cpu_util", "op": "lte", "value": 85}, "severity": "HIGH"}).json()["id"]
    r = client.post(f"/api/tenancy/governance/policies/{pid}/evaluate", headers=auth_headers,
                    json={"sample": {"cpu_util": 92}})
    assert r.json()["matched"] is False
    c = client.post("/api/tenancy/governance/compliance/run", headers=auth_headers,
                    json={"check_name": "quarterly"})
    assert c.json()["status"] in ("PASS", "FAIL")
    assert c.json()["result"]["policies"] >= 1


def test_threat_hunt_workflow(client, auth_headers):
    hid = client.post("/api/tenancy/governance/threat-hunts", headers=auth_headers, json={
        "name": "Lateral movement", "indicator": "10.0.0.5", "scope": "corp"}).json()["id"]
    r = client.post(f"/api/tenancy/governance/threat-hunts/{hid}/complete", headers=auth_headers,
                    json={"findings": [{"host": "gw-1", "severity": "HIGH"}]})
    assert r.json()["status"] == "COMPLETED"
    assert r.json()["findings"] == 1
    assert len(client.get("/api/tenancy/governance/threat-hunts", headers=auth_headers).json()) == 1


def test_service_chains(client, auth_headers):
    cid = client.post("/api/tenancy/governance/service-chains", headers=auth_headers, json={
        "name": "provision", "services": [{"service": "crm", "step": 1},
                                          {"service": "bss", "step": 2},
                                          {"service": "oss", "step": 3}]}).json()["id"]
    assert cid
    chains = client.get("/api/tenancy/governance/service-chains", headers=auth_headers).json()
    assert len(chains[0]["services"]) == 3


def test_insights_and_semantic_search(client, auth_headers):
    client.post("/api/tenancy/governance/insights", headers=auth_headers, json={
        "kind": "MARKET", "title": "North demand growing", "confidence": 0.82})
    ins = client.get("/api/tenancy/governance/insights?kind=MARKET", headers=auth_headers).json()
    assert len(ins) == 1
    client.post("/api/tenancy/governance/knowledge-docs", headers=auth_headers, json={
        "title": "Fiber installation guide", "content": "How to install fiber ONT and test signal",
        "tags": ["fttx"]})
    res = client.get("/api/tenancy/governance/knowledge-docs/search?q=fiber", headers=auth_headers).json()
    assert len(res) == 1
    assert res[0]["score"] > 0


def test_procurement_and_inventory_forecast(client, auth_headers):
    p = client.post("/api/tenancy/governance/procurement", headers=auth_headers, json={
        "vendor": "Acme", "item": "ONT", "quantity": 200, "amount": 40000})
    assert p.json()["status"] == "AUTO_CREATED"
    f = client.post("/api/tenancy/governance/inventory/forecast", headers=auth_headers, json={
        "item": "ONT", "predicted_demand": 180, "confidence": 0.7})
    assert f.json()["predicted_demand"] == 180


def test_roi_tracking(client, auth_headers):
    r = client.post("/api/tenancy/governance/roi", headers=auth_headers, json={
        "project": "FTTx North", "investment": 100000, "return_value": 150000})
    assert r.json()["roi_pct"] == 50.0
    assert len(client.get("/api/tenancy/governance/roi", headers=auth_headers).json()) == 1


def test_scaling_rules(client, auth_headers):
    client.post("/api/tenancy/governance/scaling-rules", headers=auth_headers, json={
        "service": "aaa-service", "metric": "CPU", "threshold": 80, "min_instances": 2, "max_instances": 8})
    r = client.get("/api/tenancy/governance/scaling-rules", headers=auth_headers).json()
    assert r[0]["max"] == 8


def test_mesh_link_mtls(client, auth_headers):
    r = client.post("/api/tenancy/governance/mesh-links", headers=auth_headers, json={
        "source": "bss-service", "target": "oss-service"})
    assert r.json()["mtls_enabled"] is True
    assert r.json()["status"] == "CONNECTED"


def test_cloud_abstraction_and_workload_migration(client, auth_headers):
    c = client.post("/api/tenancy/governance/cloud-providers", headers=auth_headers, json={
        "provider": "AWS", "region": "ap-south-1", "workload_name": "billing"})
    assert c.json()["abstraction_status"] == "ACTIVE"
    m = client.post("/api/tenancy/governance/workloads/migrate", headers=auth_headers,
                    json={"workload_name": "billing", "target_cloud": "GCP"})
    assert m.json()["portability_status"] == "MIGRATED"
    assert m.json()["to"] == "GCP"


def test_translation_multi_language(client, auth_headers):
    t = client.post("/api/tenancy/governance/translations", headers=auth_headers,
                    json={"text": "hello world", "target_lang": "es"})
    assert t.json()["translated_text"].startswith("hola")
    assert t.json()["target_lang"] == "es"


def test_rbac_auditor_view_only(client, tenant):
    auditor = {"Authorization": f"Bearer {make_token('AUDITOR', tenant.id)}"}
    r = client.post("/api/tenancy/governance/policies", headers=auditor, json={
        "name": "x", "rule_json": {}})
    assert r.status_code == 403
    g = client.get("/api/tenancy/governance/service-chains", headers=auditor)
    assert g.status_code == 200


def test_tenant_isolation(client, auth_headers, tenant, tenant_b):
    other = {"Authorization": f"Bearer {make_token('TENANT_ADMIN', tenant_b.id)}"}
    client.post("/api/tenancy/governance/insights", headers=auth_headers, json={
        "kind": "PRODUCT", "title": "private insight"})
    r = client.get("/api/tenancy/governance/insights", headers=other)
    assert r.json() == []
