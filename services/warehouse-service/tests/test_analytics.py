"""Warehouse analytics tests (Batch 7d: 468, 477, 478, 499, 839)."""
import uuid

from conftest import make_token


def test_kpi_management(client, headers, tenant_id):
    r = client.post("/api/warehouse/kpis", headers=headers, json={
        "code": "ARPU", "name": "Avg Revenue Per User", "target": 120.0, "unit": "USD"})
    assert r.json()["code"] == "ARPU"
    rl = client.get("/api/warehouse/kpis", headers=headers)
    assert len(rl.json()) == 1


def test_revenue_trend(client, headers, tenant_id):
    r = client.post("/api/warehouse/revenue/trends", headers=headers, json={
        "stream": "broadband", "period": "2026-08", "amount": 100000.0, "trend": 5.2})
    assert r.json()["stream"] == "broadband"
    assert r.json()["amount"] == 100000.0


def test_profitability(client, headers, tenant_id):
    r = client.post("/api/warehouse/profitability", headers=headers, json={
        "segment": "FTTH", "period": "2026-08", "revenue": 50000.0, "cost": 30000.0})
    assert r.json()["margin_pct"] == 40.0


def test_horizontal_scaling(client, headers, tenant_id):
    r = client.post("/api/warehouse/cluster/scale", headers=headers, json={
        "node": "worker-1", "load": 85.0, "scale": 10})
    assert r.json()["status"] == "SCALING"
    nodes = client.get("/api/warehouse/cluster/nodes", headers=headers).json()
    assert nodes[0]["load"] == 95.0


def test_ecosystem_analytics(client, headers, tenant_id):
    r = client.post("/api/warehouse/ecosystem/metrics", headers=headers, json={
        "partner": "acme", "period": "2026-08", "metric": "api_calls", "value": 12345.0})
    assert r.json()["value"] == 12345.0


def test_requires_auth(client, tenant_id):
    assert client.get("/api/warehouse/kpis").status_code == 401


def test_tenant_isolation(client, headers, tenant_id):
    other = {"Authorization": f"Bearer {make_token('TENANT_ADMIN', uuid.uuid4())}"}
    client.post("/api/warehouse/kpis", headers=headers, json={"code": "ARPU", "name": "ARPU"})
    assert client.get("/api/warehouse/kpis", headers=other).json() == []


def test_rbac_readonly_denied_write(client, tenant_id):
    ro = {"Authorization": f"Bearer {make_token('READ_ONLY', tenant_id)}"}
    assert client.post("/api/warehouse/kpis", headers=ro, json={"code": "X", "name": "X"}).status_code == 403
