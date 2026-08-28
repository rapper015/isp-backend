from datetime import datetime, timedelta, timezone
from uuid import uuid4
import jwt
from fastapi.testclient import TestClient
from app.main import app


def token(secret, role, tenant_id=None):
    payload = {"sub": "admin-test", "role": role, "exp": datetime.now(timezone.utc) + timedelta(minutes=5)}
    if tenant_id: payload["tenant_id"] = tenant_id
    return jwt.encode(payload, secret, algorithm="HS256")


def test_management_jwt_enforces_permissions_and_tenant_scope(monkeypatch):
    secret = "test-management-jwt-secret-at-least-32-bytes"
    monkeypatch.setenv("AAA_JWT_SECRET", secret)
    service_headers = {"X-AAA-Service-Key": "test-internal-key"}
    with TestClient(app) as client:
        first = client.post("/api/aaa/tenants", json={"name": f"rbac-a-{uuid4().hex}"}, headers=service_headers).json()["id"]
        second = client.post("/api/aaa/tenants", json={"name": f"rbac-b-{uuid4().hex}"}, headers=service_headers).json()["id"]
        billing = {"Authorization": f"Bearer {token(secret, 'billing_admin', first)}"}
        assert client.get(f"/api/aaa/usage?tenant_id={first}", headers=billing).status_code == 200
        assert client.get(f"/api/aaa/usage?tenant_id={second}", headers=billing).status_code == 403
        assert client.post("/api/aaa/nas", json={"tenant_id": first, "name": "denied", "source_ip": "203.0.113.201"}, headers=billing).status_code == 403
