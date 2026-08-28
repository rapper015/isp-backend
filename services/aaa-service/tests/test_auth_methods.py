from uuid import uuid4
from fastapi.testclient import TestClient
from app.main import app


def test_chap_is_explicitly_rejected_instead_of_using_a_pap_hash():
    headers = {"X-AAA-Service-Key": "test-internal-key"}
    with TestClient(app) as client:
        tenant_id = client.post("/api/aaa/tenants", json={"name": f"chap-{uuid4().hex}"}, headers=headers).json()["id"]
        client.post("/api/aaa/nas", json={"tenant_id": tenant_id, "name": "edge", "source_ip": "203.0.113.42", "allowed_methods": ["chap"]}, headers=headers)
        client.post("/api/aaa/credentials", json={"tenant_id": tenant_id, "subscriber_id": str(uuid4()), "username": "chap-user", "password": "password-for-pap", "allowed_methods": ["chap"]}, headers=headers)
        result = client.post("/internal/radius/v1/authenticate", headers=headers, json={"attributes": {"User-Name": "chap-user", "CHAP-Password": "opaque-chap-response", "NAS-IP-Address": "203.0.113.42"}})
        assert result.status_code == 200
        assert result.json()["decision"] == "REJECT_METHOD_NOT_ALLOWED"
