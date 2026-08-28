from uuid import uuid4
from fastapi.testclient import TestClient
from app.main import app


def test_tenant_scoped_nas_crud_and_reconciliation_are_safe_simulations():
    headers = {"X-AAA-Service-Key": "test-internal-key"}
    with TestClient(app) as client:
        tenant_id = client.post("/api/aaa/tenants", json={"name": f"operations-{uuid4().hex}"}, headers=headers).json()["id"]
        nas = client.post("/api/aaa/nas", json={"tenant_id": tenant_id, "name": "edge", "source_ip": "198.51.100.19"}, headers=headers)
        assert nas.status_code == 200
        nas_id = nas.json()["id"]
        updated = client.patch(f"/api/aaa/nas/{nas_id}?tenant_id={tenant_id}", json={"vendor": "mikrotik", "coa_port": 3799}, headers=headers)
        assert updated.status_code == 200
        assert client.post(f"/api/aaa/nas/{nas_id}/disable?tenant_id={tenant_id}", headers=headers).json()["enabled"] is False
        assert client.post(f"/api/aaa/nas/{nas_id}/enable?tenant_id={tenant_id}", headers=headers).json()["enabled"] is True
        reconciled = client.post(f"/api/aaa/sessions/reconcile?tenant_id={tenant_id}", json={"nas_id": nas_id, "active_session_ids": ["router-only"]}, headers=headers)
        assert reconciled.status_code == 200
        assert reconciled.json()["simulation"] is True
        assert reconciled.json()["router_only"] == ["router-only"]


def test_credential_lifecycle_never_returns_password_material():
    headers = {"X-AAA-Service-Key": "test-internal-key"}
    with TestClient(app) as client:
        tenant_id = client.post("/api/aaa/tenants", json={"name": f"credentials-{uuid4().hex}"}, headers=headers).json()["id"]
        credential = client.post("/api/aaa/credentials", json={"tenant_id": tenant_id, "subscriber_id": str(uuid4()), "username": "credential-user", "password": "safe-password-value"}, headers=headers)
        assert credential.status_code == 200
        credential_id = credential.json()["id"]
        changed = client.patch(f"/api/aaa/credentials/{credential_id}?tenant_id={tenant_id}", json={"status": "disabled"}, headers=headers)
        assert changed.status_code == 200
        revoked = client.post(f"/api/aaa/credentials/{credential_id}/revoke?tenant_id={tenant_id}", headers=headers)
        assert revoked.status_code == 200
        assert revoked.json()["status"] == "revoked"
        assert "password" not in revoked.text


def test_nas_coa_test_requires_a_session_owned_by_that_nas():
    headers = {"X-AAA-Service-Key": "test-internal-key"}
    with TestClient(app) as client:
        tenant_id = client.post("/api/aaa/tenants", json={"name": f"coa-test-{uuid4().hex}"}, headers=headers).json()["id"]
        nas_id = client.post("/api/aaa/nas", json={"tenant_id": tenant_id, "name": "edge", "source_ip": "198.51.100.89"}, headers=headers).json()["id"]
        accounting = client.post("/internal/radius/v1/accounting", headers=headers, json={"idempotency_key": "coa-session-start", "attributes": {"User-Name": "unknown", "NAS-IP-Address": "198.51.100.89", "Acct-Session-Id": "test-coa", "Acct-Status-Type": "Start"}})
        assert accounting.status_code == 200
        session_id = client.get(f"/api/aaa/sessions?tenant_id={tenant_id}", headers=headers).json()[0]["id"]
        queued = client.post(f"/api/aaa/nas/{nas_id}/test-coa?tenant_id={tenant_id}&session_id={session_id}&idempotency_key=coa-capability", headers=headers)
        assert queued.status_code == 200
        assert queued.json()["test_queued"] is True
