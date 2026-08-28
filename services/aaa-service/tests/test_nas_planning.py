from uuid import uuid4
from fastapi.testclient import TestClient
from app.main import app

def _nas(client, headers):
    tenant_id = client.post("/api/aaa/tenants", json={"name": f"plan-{uuid4().hex}"}, headers=headers).json()["id"]
    nas_id = client.post("/api/nas", headers=headers, json={"tenant_id": tenant_id, "name": "router", "management_host": "10.50.2.2", "routeros_username": "u", "routeros_password": "p", "radius_source_ip": "10.50.2.2"}).json()["id"]
    server_host = f"198.51.100.{(uuid4().int % 200) + 1}"
    server_id = client.post("/api/aaa/radius-servers", headers=headers, json={"name": f"radius-{uuid4().hex}", "host": server_host, "internal_api_key": "not-a-real-radius-server-key"}).json()["id"]
    assignment = client.post(f"/api/nas/{nas_id}/radius-assignments?tenant_id={tenant_id}", headers=headers, json={"radius_server_id": server_id, "role": "primary", "services": ["pppoe"]})
    assert assignment.status_code == 200
    return tenant_id, nas_id

def test_nas_plan_is_secret_free_and_apply_is_idempotent(monkeypatch):
    monkeypatch.setenv("AAA_ENCRYPTION_KEY", "K2HWufrlmhAt4fF3tP7i3VFUXupdsxhhlRP9Aw7-Ctg=")
    monkeypatch.setenv("NAS_APPROVED_NETWORKS", "10.50.0.0/16")
    headers = {"X-AAA-Service-Key": "test-internal-key"}
    with TestClient(app) as client:
        tenant_id, nas_id = _nas(client, headers)
        desired = client.post(f"/api/nas/{nas_id}/desired-configuration?tenant_id={tenant_id}", headers=headers, json={"services": ["pppoe"], "ppp_aaa": True, "interim_update_seconds": 600})
        assert desired.status_code == 200 and "password" not in desired.text and "secret" not in desired.text
        plan = client.post(f"/api/nas/{nas_id}/plan?tenant_id={tenant_id}", headers=headers)
        assert plan.status_code == 200 and plan.json()["validation"]["valid"] is True
        plan_id = plan.json()["id"]
        first = client.post(f"/api/nas/{nas_id}/plans/{plan_id}/apply?tenant_id={tenant_id}", headers=headers, json={"idempotency_key": "apply-once"})
        second = client.post(f"/api/nas/{nas_id}/plans/{plan_id}/apply?tenant_id={tenant_id}", headers=headers, json={"idempotency_key": "apply-once"})
        assert first.status_code == second.status_code == 200
        assert first.json()["job_id"] == second.json()["job_id"] and second.json()["duplicate"] is True

def test_login_radius_plan_requires_break_glass(monkeypatch):
    monkeypatch.setenv("AAA_ENCRYPTION_KEY", "K2HWufrlmhAt4fF3tP7i3VFUXupdsxhhlRP9Aw7-Ctg=")
    monkeypatch.setenv("NAS_APPROVED_NETWORKS", "10.50.0.0/16")
    headers = {"X-AAA-Service-Key": "test-internal-key"}
    with TestClient(app) as client:
        tenant_id, nas_id = _nas(client, headers)
        client.post(f"/api/nas/{nas_id}/desired-configuration?tenant_id={tenant_id}", headers=headers, json={"login_radius": True})
        plan = client.post(f"/api/nas/{nas_id}/plan?tenant_id={tenant_id}", headers=headers)
        assert plan.status_code == 200
        assert plan.json()["risk"] == "critical"
        assert plan.json()["validation"]["valid"] is False
        assert "break-glass" in " ".join(plan.json()["validation"]["errors"])
