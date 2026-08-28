from uuid import uuid4
from fastapi.testclient import TestClient
from app.main import app

def test_create_nas_draft_encrypts_routeros_credential(monkeypatch):
    monkeypatch.setenv("AAA_ENCRYPTION_KEY", "K2HWufrlmhAt4fF3tP7i3VFUXupdsxhhlRP9Aw7-Ctg=")
    monkeypatch.setenv("NAS_APPROVED_NETWORKS", "10.50.0.0/16")
    headers = {"X-AAA-Service-Key": "test-internal-key"}
    with TestClient(app) as client:
        tenant_id = client.post("/api/aaa/tenants", json={"name": f"nas-draft-{uuid4().hex}"}, headers=headers).json()["id"]
        response = client.post("/api/nas", headers=headers, json={"tenant_id": tenant_id, "name": "pop-router", "management_host": "10.50.1.2", "routeros_username": "api-user", "routeros_password": "not-returned", "radius_source_ip": "10.50.1.2", "services": ["pppoe"]})
        assert response.status_code == 200
        assert response.json()["lifecycle_status"] == "DRAFT"
        assert "not-returned" not in response.text

def test_radius_assignment_never_returns_its_shared_secret(monkeypatch):
    monkeypatch.setenv("AAA_ENCRYPTION_KEY", "K2HWufrlmhAt4fF3tP7i3VFUXupdsxhhlRP9Aw7-Ctg=")
    monkeypatch.setenv("NAS_APPROVED_NETWORKS", "10.50.0.0/16")
    headers = {"X-AAA-Service-Key": "test-internal-key"}
    with TestClient(app) as client:
        tenant_id = client.post("/api/aaa/tenants", json={"name": f"assignment-{uuid4().hex}"}, headers=headers).json()["id"]
        nas_id = client.post("/api/nas", headers=headers, json={"tenant_id": tenant_id, "name": "router", "management_host": "10.50.1.3", "routeros_username": "u", "routeros_password": "p", "radius_source_ip": "10.50.1.3"}).json()["id"]
        server_id = client.post("/api/aaa/radius-servers", headers=headers, json={"name": f"radius-{uuid4().hex}", "host": "192.0.2.40", "internal_api_key": "not-a-real-radius-server-key"}).json()["id"]
        created = client.post(f"/api/nas/{nas_id}/radius-assignments?tenant_id={tenant_id}", headers=headers, json={"radius_server_id": server_id, "role": "primary", "services": ["pppoe"]})
        assert created.status_code == 200
        assert "secret" not in created.text.replace("secret_displayed", "")
