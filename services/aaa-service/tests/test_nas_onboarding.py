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
