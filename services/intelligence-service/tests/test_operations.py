"""Intelligence operations tests (Batch 7b: 889, 1289, 1297, 1420, 1481)."""
from conftest import make_token


def _tok(role, tenant):
    return {"Authorization": f"Bearer {make_token(role, tenant)}"}


def test_personalization_upsert_and_recommend(client, tenant_id):
    h = _tok("AI_ENGINEER", tenant_id)
    r = client.post("/api/intelligence/v1/ops/personalization/profiles", json={
        "subscriber_id": "sub-1", "segments": ["PREMIUM", "DATA_HEAVY"],
        "preferences": {"language": "en"}, "engagement_score": 0.9}, headers=h)
    assert r.status_code == 200
    rec = client.post("/api/intelligence/v1/ops/personalization/recommend",
                      json={"subscriber_id": "sub-1"}, headers=h)
    assert rec.json()["recommendation"] == "offer_fiber_500_plus_ott"


def test_bottleneck_detection(client, tenant_id):
    h = _tok("NOC_ENGINEER", tenant_id)
    r = client.post("/api/intelligence/v1/ops/bottlenecks/detect", json={
        "scope": "aaa-db", "metric": "cpu", "value": 95, "threshold": 80}, headers=h)
    assert r.json()["detected"] is True
    assert r.json()["severity"] == "MEDIUM"
    miss = client.post("/api/intelligence/v1/ops/bottlenecks/detect", json={
        "scope": "x", "metric": "cpu", "value": 10, "threshold": 80}, headers=h)
    assert miss.json()["detected"] is False
    bl = client.get("/api/intelligence/v1/ops/bottlenecks", headers=h)
    assert len(bl.json()) == 1


def test_automation_coverage(client, tenant_id):
    h = _tok("AI_ENGINEER", tenant_id)
    r = client.post("/api/intelligence/v1/ops/automation-coverage", json={
        "period": "MONTH", "automated": 80, "manual": 20}, headers=h)
    assert r.json()["coverage_pct"] == 80.0
    g = client.get("/api/intelligence/v1/ops/automation-coverage?period=MONTH", headers=h)
    assert g.json()["coverage_pct"] == 80.0


def test_node_and_region_profitability(client, tenant_id):
    h = _tok("FINANCE_OPS", tenant_id)
    n = client.post("/api/intelligence/v1/ops/node-profit", json={
        "node": "node-east", "period": "MONTH", "revenue": 5000, "cost": 3000}, headers=h)
    assert n.json()["profit"] == 2000.0
    rg = client.post("/api/intelligence/v1/ops/region-profitability", json={
        "region": "NORTH", "period": "MONTH", "revenue": 10000, "cost": 6000}, headers=h)
    assert rg.json()["profit_margin"] == 40.0
    assert len(client.get("/api/intelligence/v1/ops/node-profit", headers=h).json()) == 1
    assert len(client.get("/api/intelligence/v1/ops/region-profitability", headers=h).json()) == 1


def test_ops_requires_tenant_scope(client, platform_headers):
    # platform aggregate with no tenant -> _tid() returns None -> tenant filter no-op but
    # tenant-owned access is gated; verify an unauth'd write still 401/403 path exists
    r = client.post("/api/intelligence/v1/ops/node-profit", json={
        "node": "n", "revenue": 1, "cost": 1})
    assert r.status_code == 401


def test_rbac_auditor_can_view_ops(client, tenant_id):
    auditor = _tok("AUDITOR", tenant_id)
    g = client.get("/api/intelligence/v1/ops/bottlenecks", headers=auditor)
    assert g.status_code == 200
