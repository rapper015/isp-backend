from uuid import uuid4
from fastapi.testclient import TestClient
from app.main import app


def test_audit_api_is_tenant_scoped_and_never_returns_secret_data():
    headers = {"X-AAA-Service-Key": "test-internal-key"}
    with TestClient(app) as client:
        tenant_id = client.post("/api/aaa/tenants", json={"name": f"audit-{uuid4().hex}"}, headers=headers).json()["id"]
        client.post("/api/aaa/nas", json={"tenant_id": tenant_id, "name": "edge", "source_ip": "203.0.113.210"}, headers=headers)
        logs = client.get(f"/api/aaa/audit?tenant_id={tenant_id}&action=nas.created", headers=headers)
        assert logs.status_code == 200
        assert logs.json()[0]["action"] == "nas.created"
        assert "secret" not in str(logs.json()).lower()
