from uuid import uuid4
from fastapi.testclient import TestClient
from app.main import app


def test_authorization_leases_an_ip_from_tenant_policy_pool():
    headers = {"X-AAA-Service-Key": "test-internal-key"}
    with TestClient(app) as client:
        tenant_id = client.post("/api/aaa/tenants", json={"name": f"ip-auth-{uuid4().hex}", "policy": {"default_policy": {"ipv4_pool": "dynamic"}}}, headers=headers).json()["id"]
        client.post("/api/aaa/nas", json={"tenant_id": tenant_id, "name": "edge", "source_ip": "198.51.100.30"}, headers=headers)
        client.post("/api/aaa/ip-pools", json={"tenant_id": tenant_id, "name": "dynamic", "cidr": "10.77.0.0/30"}, headers=headers)
        client.post("/api/aaa/credentials", json={"tenant_id": tenant_id, "subscriber_id": str(uuid4()), "username": "leased", "password": "leased-password"}, headers=headers)
        result = client.post("/internal/radius/v1/authorize", headers=headers, json={"attributes": {"User-Name": "leased", "NAS-IP-Address": "198.51.100.30"}})
        assert result.status_code == 200
        assert result.json()["reply_attributes"]["Framed-IP-Address"] == "10.77.0.1"
