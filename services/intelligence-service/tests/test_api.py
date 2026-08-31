"""API endpoint flows for the Intelligence Service."""
import uuid
from datetime import datetime, timezone

from conftest import make_token


def _now():
    return datetime.now(timezone.utc)


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_contracts_seeded_via_api(client, platform_headers):
    resp = client.get("/api/intelligence/v1/contracts", headers=platform_headers)
    assert resp.status_code == 200
    names = {c["event_name"] for c in resp.json()}
    assert "billing.payment.failed.v1" in names


def test_ingest_via_api(client, platform_headers, tenant_id):
    resp = client.post("/api/intelligence/v1/ingest", headers=platform_headers, json={
        "event_type": "billing.payment.captured.v1", "tenant_id": str(tenant_id),
        "payload": {"customer_id": "c-1", "amount": 100}})
    assert resp.status_code == 200, resp.text
    assert resp.json()["state"] == "VALID"


def test_raw_events_via_api(client, platform_headers, tenant_id):
    client.post("/api/intelligence/v1/ingest", headers=platform_headers, json={
        "event_type": "billing.payment.failed.v1", "tenant_id": str(tenant_id),
        "payload": {"customer_id": "c-2", "reason": "x"}})
    resp = client.get("/api/intelligence/v1/raw-events", headers=platform_headers)
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_quality_run_via_api(client, platform_headers, tenant_id):
    client.post("/api/intelligence/v1/ingest", headers=platform_headers, json={
        "event_type": "billing.payment.captured.v1", "tenant_id": str(tenant_id),
        "payload": {"customer_id": "c-1"}})
    resp = client.post("/api/intelligence/v1/quality/run", headers=platform_headers,
                       params={"contract": "billing.payment.captured.v1"})
    assert resp.status_code == 200
    assert resp.json()["overall"] == "PASS"


def test_dataset_and_training_via_api(client, platform_headers, tenant_id):
    for i in range(10):
        client.post("/api/intelligence/v1/ingest", headers=platform_headers, json={
            "event_type": "crm.customer.created.v1", "tenant_id": str(tenant_id),
            "payload": {"customer_id": f"c-{i}", "recent_payment_failures": i % 3,
                        "support_ticket_count": i % 2, "churned": 1 if i % 4 == 0 else 0}})
    snap = client.post("/api/intelligence/v1/datasets", headers=platform_headers, json={
        "code": "ds-api", "contracts": ["crm.customer.created.v1"]})
    assert snap.status_code == 200, snap.text
    snap_id = snap.json()["id"]
    resp = client.post("/api/intelligence/v1/training", headers=platform_headers, json={
        "model_code": "api_model", "snapshot_id": snap_id, "algorithm": "WEIGHTED_LOGIT",
        "feature_names": ["recent_payment_failures", "support_ticket_count"],
        "parameters": {"intercept": -0.3, "weights": {"recent_payment_failures": 0.6,
                                                      "support_ticket_count": 0.3}},
        "use_case": "CHURN", "decision_threshold": 0.5})
    assert resp.status_code == 200, resp.text
    assert "model_id" in resp.json()


def test_model_approve_deploy_flow(client, platform_headers, tenant_id):
    for i in range(10):
        client.post("/api/intelligence/v1/ingest", headers=platform_headers, json={
            "event_type": "crm.customer.created.v1", "tenant_id": str(tenant_id),
            "payload": {"customer_id": f"c-{i}", "churned": 1 if i % 3 == 0 else 0}})
    snap_id = client.post("/api/intelligence/v1/datasets", headers=platform_headers, json={
        "code": "ds-deploy", "contracts": ["crm.customer.created.v1"]}).json()["id"]
    model_id = client.post("/api/intelligence/v1/training", headers=platform_headers, json={
        "model_code": "deploy_model", "snapshot_id": snap_id, "use_case": "CHURN",
        "feature_names": [], "parameters": {}}).json()["model_id"]
    resp = client.post(f"/api/intelligence/v1/models/{model_id}/approve", headers=platform_headers)
    assert resp.json()["approval_status"] == "APPROVED"
    resp = client.post(f"/api/intelligence/v1/models/{model_id}/deploy", headers=platform_headers,
                       json={"environment": "SHADOW"})
    assert resp.json()["environment"] == "SHADOW"
    resp = client.post(f"/api/intelligence/v1/models/{model_id}/rollback", headers=platform_headers)
    assert resp.json()["state"] == "ROLLED_BACK"


def test_fraud_via_api(client, platform_headers, tenant_id):
    resp = client.post("/api/intelligence/v1/fraud/evaluate", headers=platform_headers, json={
        "subject_type": "subscriber", "subject": "sub-1",
        "record": {"auth_failure_rate": 0.95}})
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    assert resp.json()[0]["risk_score"] > 0


def test_churn_via_api(client, platform_headers, tenant_id):
    client.post("/api/intelligence/v1/ingest", headers=platform_headers, json={
        "event_type": "crm.customer.created.v1", "tenant_id": str(tenant_id),
        "payload": {"customer_id": "c-1", "recent_payment_failures": 2}})
    resp = client.post("/api/intelligence/v1/churn/score", headers=platform_headers, json={
        "customer_ref": "c-1", "horizon_days": 30})
    assert resp.status_code == 200, resp.text
    assert "risk_band" in resp.json()


def test_maintenance_via_api(client, platform_headers, tenant_id):
    client.post("/api/intelligence/v1/ingest", headers=platform_headers, json={
        "event_type": "nas.health_changed.v1", "tenant_id": str(tenant_id),
        "payload": {"nas_id": "nas-1", "error_rate": 0.7, "latency_avg_ms": 800}})
    resp = client.post("/api/intelligence/v1/maintenance/predict", headers=platform_headers, json={
        "asset_type": "nas", "asset_ref": "nas-1", "horizon_days": 14})
    assert resp.status_code == 200
    assert "failure_probability" in resp.json()


def test_capacity_via_api(client, platform_headers, tenant_id):
    resp = client.post("/api/intelligence/v1/capacity/forecast", headers=platform_headers, json={
        "resource_type": "ip_pool", "resource_ref": "pool-1",
        "utilization_series": [0.5, 0.6, 0.7, 0.8, 0.9], "horizon_days": 10})
    assert resp.status_code == 200
    assert "risk" in resp.json()


def test_remediation_l3_via_api(client, sre_headers, tenant_id):
    resp = client.post("/api/intelligence/v1/remediation/intents", headers=sre_headers, json={
        "policy_code": "retry_telemetry_collection", "target_type": "nas", "target_ref": "nas-1",
        "payload": {"device_reachable": True}, "idempotency_key": "api-1"})
    assert resp.status_code == 200, resp.text
    intent_id = resp.json()["intent_id"]
    assert resp.json()["autonomy_level"] == "L3"
    resp = client.post(f"/api/intelligence/v1/remediation/intents/{intent_id}/execute", headers=sre_headers)
    assert resp.json()["state"] == "STARTED"
    resp = client.post(f"/api/intelligence/v1/remediation/intents/{intent_id}/complete", headers=sre_headers,
                       json={"result": "SUCCESS", "verification": "verified"})
    assert resp.json()["state"] == "COMPLETED"


def test_kill_switch_via_api(client, platform_headers, tenant_id):
    resp = client.get("/api/intelligence/v1/kill-switch", headers=platform_headers)
    assert resp.json()["global"] is False
    resp = client.post("/api/intelligence/v1/kill-switch", headers=platform_headers, json={
        "scope": "GLOBAL", "enabled": True, "reason": "test"})
    assert resp.json()["enabled"] is True
    resp = client.get("/api/intelligence/v1/kill-switch", headers=platform_headers)
    assert resp.json()["global"] is True


def test_internal_ingest_requires_key(client):
    resp = client.post("/internal/intelligence/v1/ingest/event", json={"event_type": "x"})
    assert resp.status_code == 401


def test_insights_via_api(client, tenant_headers, platform_headers, tenant_id):
    client.post("/api/intelligence/v1/fraud/evaluate", headers=platform_headers, json={
        "subject_type": "subscriber", "subject": "sub-1", "tenant_id": str(tenant_id),
        "record": {"auth_failure_rate": 0.9}})
    resp = client.get("/api/intelligence/v1/insights", headers=tenant_headers)
    assert resp.status_code == 200
    assert "fraud_signals" in resp.json()
