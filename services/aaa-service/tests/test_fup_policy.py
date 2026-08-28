from uuid import uuid4
from fastapi.testclient import TestClient
from app.main import app


def test_fup_policy_overrides_rate_limit_in_authorization_response():
    headers = {"X-AAA-Service-Key": "test-internal-key"}
    with TestClient(app) as client:
        tenant_id = client.post("/api/aaa/tenants", headers=headers, json={"name": f"fup-policy-{uuid4().hex}", "policy": {"default_policy": {"upload_kbps": 10000, "download_kbps": 20000, "fup_threshold_bytes": 1, "fup_policy": {"upload_kbps": 1000, "download_kbps": 2000}}}}).json()["id"]
        client.post("/api/aaa/nas", headers=headers, json={"tenant_id": tenant_id, "name": "edge", "source_ip": "203.0.113.55"})
        subscriber_id = str(uuid4())
        client.post("/api/aaa/credentials", headers=headers, json={"tenant_id": tenant_id, "subscriber_id": subscriber_id, "username": "fup-user", "password": "fup-password-value"})
        client.post("/internal/radius/v1/accounting", headers=headers, json={"idempotency_key": "fup-start", "attributes": {"User-Name": "fup-user", "NAS-IP-Address": "203.0.113.55", "Acct-Session-Id": "fup-session", "Acct-Status-Type": "Start", "Acct-Input-Octets": 2}})
        result = client.post("/internal/radius/v1/authorize", headers=headers, json={"attributes": {"User-Name": "fup-user", "NAS-IP-Address": "203.0.113.55"}})
        assert result.status_code == 200
        # RouterOS rx/tx: download/upload (M3 direction-correct format).
        assert result.json()["reply_attributes"]["Mikrotik-Rate-Limit"] == "2M/1M"


def test_usage_reset_clears_fup_projection():
    headers = {"X-AAA-Service-Key": "test-internal-key"}
    with TestClient(app) as client:
        tenant_id = client.post("/api/aaa/tenants", headers=headers, json={"name": f"fup-reset-{uuid4().hex}", "policy": {"default_policy": {"fup_threshold_bytes": 1}}}).json()["id"]
        client.post("/api/aaa/nas", headers=headers, json={"tenant_id": tenant_id, "name": "edge", "source_ip": "203.0.113.56"})
        subscriber_id = str(uuid4())
        client.post("/api/aaa/credentials", headers=headers, json={"tenant_id": tenant_id, "subscriber_id": subscriber_id, "username": "reset-user", "password": "reset-password-value"})
        client.post("/internal/radius/v1/accounting", headers=headers, json={"idempotency_key": "reset-start", "attributes": {"User-Name": "reset-user", "NAS-IP-Address": "203.0.113.56", "Acct-Session-Id": "reset-session", "Acct-Status-Type": "Start", "Acct-Input-Octets": 2}})
        reset = client.post(f"/api/aaa/usage/subscribers/{subscriber_id}/reset?tenant_id={tenant_id}", headers=headers, json={"idempotency_key": "manual-reset"})
        assert reset.status_code == 200
        usage = client.get(f"/api/aaa/usage/subscribers/{subscriber_id}?tenant_id={tenant_id}", headers=headers).json()[0]
        assert (usage["input_octets"], usage["output_octets"], usage["fup_active"]) == (0, 0, False)
