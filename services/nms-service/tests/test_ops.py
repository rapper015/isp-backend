"""NMS ops tests (Batch 7c: 266, 1082, 1124, 1167, 1286, 1344)."""
import uuid

from conftest import make_token


def test_escalation_policies(client, headers, tenant_id):
    r = client.post("/api/nms/ops/escalation-policies", headers=headers, json={
        "name": "P1", "rule_json": {"severity": "HIGH", "steps": ["page-oncall", "notify-sre"]}})
    assert r.status_code == 200
    assert r.json()["name"] == "P1"
    rl = client.get("/api/nms/ops/escalation-policies", headers=headers)
    assert len(rl.json()) == 1


def test_config_diff_viewer(client, headers, tenant_id):
    client.post("/api/nms/ops/config/snapshot", headers=headers, json={
        "device_id": "gw-1", "label": "BASELINE", "config": "interface 1\n  ip 10.0.0.1\n"})
    client.post("/api/nms/ops/config/snapshot", headers=headers, json={
        "device_id": "gw-1", "label": "CURRENT", "config": "interface 1\n  ip 10.0.0.2\n  description x\n"})
    r = client.post("/api/nms/ops/config/diff", headers=headers, json={"device_id": "gw-1"})
    assert r.json()["drift"] is True
    assert len(r.json()["diff"]) > 0


def test_config_diff_no_drift(client, headers, tenant_id):
    client.post("/api/nms/ops/config/snapshot", headers=headers, json={
        "device_id": "gw-2", "label": "BASELINE", "config": "same\n"})
    client.post("/api/nms/ops/config/snapshot", headers=headers, json={
        "device_id": "gw-2", "label": "CURRENT", "config": "same\n"})
    r = client.post("/api/nms/ops/config/diff", headers=headers, json={"device_id": "gw-2"})
    assert r.json()["drift"] is False


def test_approval_sla_overdue(client, headers, tenant_id):
    client.post("/api/nms/ops/approval-sla", headers=headers, json={
        "approval_type": "MAINTENANCE", "sla_minutes": 120})
    r = client.post("/api/nms/ops/approval-sla/overdue", headers=headers, json={
        "approval_type": "MAINTENANCE", "minutes": 180})
    assert r.json()["overdue_count"] == 1
    r2 = client.post("/api/nms/ops/approval-sla/overdue", headers=headers, json={
        "approval_type": "MAINTENANCE", "minutes": 60})
    assert r2.json()["overdue_count"] == 1  # on-time doesn't increment


def test_cache_strategy(client, headers, tenant_id):
    r = client.post("/api/nms/ops/cache-strategies", headers=headers, json={
        "cache_key": "device_health", "ttl_seconds": 60, "strategy": "LRU"})
    assert r.json()["ttl_seconds"] == 60


def test_degradation_rule(client, headers, tenant_id):
    r = client.post("/api/nms/ops/degradation-rules", headers=headers, json={
        "service": "billing-api", "degraded_mode": "REDUCE_CONCURRENCY", "keep_alive_pct": 40})
    assert r.json()["enabled"] is True
    assert r.json()["keep_alive_pct"] == 40.0


def test_queue_saturation_protection(client, headers, tenant_id):
    r = client.post("/api/nms/ops/queue-saturation", headers=headers, json={
        "queue": "events", "depth": 1000, "max_depth": 1000})
    assert r.json()["protected"] is True
    r2 = client.post("/api/nms/ops/queue-saturation", headers=headers, json={
        "queue": "events", "depth": 100, "max_depth": 1000})
    assert r2.json()["protected"] is False


def test_requires_auth(client, tenant_id):
    r = client.get("/api/nms/ops/escalation-policies")
    assert r.status_code == 401


def test_tenant_isolation(client, headers, tenant_id):
    other = {"Authorization": f"Bearer {make_token('TENANT_ADMIN', uuid.uuid4())}"}
    client.post("/api/nms/ops/escalation-policies", headers=headers, json={
        "name": "P1", "rule_json": {}})
    rl = client.get("/api/nms/ops/escalation-policies", headers=other)
    assert rl.json() == []


def test_rbac_auditor_denied_write(client, tenant_id):
    auditor = {"Authorization": f"Bearer {make_token('AUDITOR', tenant_id)}"}
    r = client.post("/api/nms/ops/escalation-policies", headers=auditor, json={
        "name": "X", "rule_json": {}})
    assert r.status_code == 403
