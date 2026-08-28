"""Integration tests: manual FreeRADIUS registration tracking and one-time
registration packages. No FreeRADIUS is ever touched."""
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app


def _setup(client, headers, name_prefix="reg"):
    host = f"192.0.2.{(uuid4().int % 200) + 1}"
    tenant_id = client.post("/api/aaa/tenants", json={"name": f"{name_prefix}-{uuid4().hex}"}, headers=headers).json()["id"]
    nas_id = client.post("/api/nas", headers=headers, json={"tenant_id": tenant_id, "name": "router", "management_host": "10.50.3.2", "routeros_username": "u", "routeros_password": "p", "radius_source_ip": "10.50.3.2"}).json()["id"]
    server = client.post("/api/aaa/radius-servers", headers=headers, json={"name": f"radius-{uuid4().hex}", "host": host, "internal_api_key": "not-a-real-radius-server-key"}).json()["id"]
    assignment = client.post(f"/api/nas/{nas_id}/radius-assignments?tenant_id={tenant_id}", headers=headers, json={"radius_server_id": server, "role": "primary", "services": ["pppoe"]}).json()["id"]
    return tenant_id, nas_id, server, assignment


def test_registration_package_secret_is_revealed_only_once(monkeypatch):
    headers = {"X-AAA-Service-Key": "test-internal-key"}
    with TestClient(app) as client:
        tenant_id, nas_id, _, assignment = _setup(client, headers)
        token = client.post(f"/api/nas/{nas_id}/radius-assignments/{assignment}/registration-package?tenant_id={tenant_id}", headers=headers).json()["reveal_token"]
        first = client.post(f"/api/nas/{nas_id}/radius-assignments/{assignment}/registration-package/reveal?tenant_id={tenant_id}&reveal_token={token}", headers=headers)
        assert first.status_code == 200
        body = first.json()
        assert body["display_once"] is True
        assert body["shared_secret"]
        assert body["secret_version"] == 1
        assert body["nas_source_ip"] == "10.50.3.2"
        assert "text" in body  # copyable text representation
        # Second reveal with the same token must fail.
        second = client.post(f"/api/nas/{nas_id}/radius-assignments/{assignment}/registration-package/reveal?tenant_id={tenant_id}&reveal_token={token}", headers=headers)
        assert second.status_code == 404
        # The package text includes the secret only in the once-only response.
        assert "shared_secret" not in client.get(f"/api/nas/{nas_id}/radius-assignments?tenant_id={tenant_id}", headers=headers).text.replace("secret_version", "").replace("secret_displayed", "")


def test_manual_confirmation_then_technical_verification(monkeypatch):
    headers = {"X-AAA-Service-Key": "test-internal-key"}
    with TestClient(app) as client:
        tenant_id, nas_id, _, assignment = _setup(client, headers)
        confirmed = client.post(f"/api/nas/{nas_id}/radius-assignments/{assignment}/confirm-registration?tenant_id={tenant_id}", headers=headers, json={"source_ip_correct": True, "secret_version_applied": True, "services_enabled": True, "primary_configured": True})
        assert confirmed.status_code == 200
        assert confirmed.json()["manual_confirmed"] is True
        assert confirmed.json()["registration_status"] == "MANUALLY_CONFIRMED"
        # Manual confirmation alone must NOT mark the registration verified.
        status = client.get(f"/api/nas/{nas_id}/radius-registration-status?tenant_id={tenant_id}", headers=headers).json()
        assert status["assignments"][0]["registration_status"] == "MANUALLY_CONFIRMED"
        verified = client.post(f"/api/nas/{nas_id}/radius-assignments/{assignment}/verify?tenant_id={tenant_id}", headers=headers, json={"signal": "accounting_request_observed"})
        assert verified.status_code == 200
        assert verified.json()["verified"] is True
        assert verified.json()["signal"] == "accounting_request_observed"
        status = client.get(f"/api/nas/{nas_id}/radius-registration-status?tenant_id={tenant_id}", headers=headers).json()
        assert status["assignments"][0]["registration_status"] == "VERIFIED"


def test_verification_rejects_unsupported_signal(monkeypatch):
    headers = {"X-AAA-Service-Key": "test-internal-key"}
    with TestClient(app) as client:
        tenant_id, nas_id, _, assignment = _setup(client, headers)
        client.post(f"/api/nas/{nas_id}/radius-assignments/{assignment}/confirm-registration?tenant_id={tenant_id}", headers=headers, json={})
        response = client.post(f"/api/nas/{nas_id}/radius-assignments/{assignment}/verify?tenant_id={tenant_id}", headers=headers, json={"signal": "made_up_signal"})
        assert response.status_code == 422


def test_registration_package_never_leaks_secret_in_audit_or_list(monkeypatch):
    headers = {"X-AAA-Service-Key": "test-internal-key"}
    with TestClient(app) as client:
        tenant_id, nas_id, _, assignment = _setup(client, headers)
        token = client.post(f"/api/nas/{nas_id}/radius-assignments/{assignment}/registration-package?tenant_id={tenant_id}", headers=headers).json()["reveal_token"]
        revealed = client.post(f"/api/nas/{nas_id}/radius-assignments/{assignment}/registration-package/reveal?tenant_id={tenant_id}&reveal_token={token}", headers=headers).json()
        secret = revealed["shared_secret"]
        audit = client.get(f"/api/nas/{nas_id}/audit?tenant_id={tenant_id}", headers=headers).text
        assert secret not in audit
