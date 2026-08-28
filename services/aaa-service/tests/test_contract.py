from uuid import uuid4
from fastapi.testclient import TestClient
from app.main import app

def test_private_radius_contract_accepts_tenant_scoped_subscriber():
    headers = {"X-AAA-Service-Key": "test-internal-key"}
    with TestClient(app) as client:
        tenant = client.post("/api/aaa/tenants", json={"name": f"tenant-{uuid4().hex}", "policy": {"default_policy": {"reply_attributes": {"Mikrotik-Rate-Limit": "10M/20M", "Unknown": "blocked"}}}}, headers=headers)
        assert tenant.status_code == 200
        tenant_id = tenant.json()["id"]
        nas = client.post("/api/aaa/nas", json={"tenant_id": tenant_id, "name": "edge-1", "source_ip": "10.10.10.1"}, headers=headers)
        assert nas.status_code == 200
        credential = client.post("/api/aaa/credentials", json={"tenant_id": tenant_id, "subscriber_id": str(uuid4()), "username": "Alice", "password": "very-safe-test-password"}, headers=headers)
        assert credential.status_code == 200
        response = client.post("/internal/radius/v1/authenticate", json={"attributes": {"User-Name": " ALICE ", "User-Password": "very-safe-test-password", "NAS-IP-Address": "10.10.10.1", "Service-Type": "pppoe"}}, headers=headers)
        assert response.status_code == 200
        assert response.json()["outcome"] == "Access-Accept"
        assert response.json()["reply_attributes"] == {"Mikrotik-Rate-Limit": "10M/20M"}

def test_private_radius_contract_rejects_unknown_nas():
    with TestClient(app) as client:
        response = client.post("/internal/radius/v1/authenticate", json={"attributes": {"User-Name": "anyone", "User-Password": "not-the-right-password", "NAS-IP-Address": "192.0.2.250"}}, headers={"X-AAA-Service-Key": "test-internal-key"})
        assert response.status_code == 200
        assert response.json()["decision"] == "REJECT_UNKNOWN_NAS"
