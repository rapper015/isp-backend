"""Security: RBAC enforcement (reboot, factory reset, firmware upload), secret
redaction/encryption, and cross-tenant API isolation."""
import os

import pytest
from fastapi.testclient import TestClient

from app.domain import secrets
from app.main import app
from conftest import make_token


@pytest.fixture
def client(defaults):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def seeded_device(session, tenant_id, acs, make_acs_device, make_device):
    device, acs_device_id = make_device(serial="SN-SEC", product_class="AN5506")
    return {"device_id": str(device.id), "tenant_id": tenant_id}


def _headers(tenant_id, role="ISP_ADMIN"):
    return {"Authorization": f"Bearer {make_token(role, tenant_id)}"}


def test_secret_encryption_roundtrip():
    encrypted = secrets.encrypt_secret("P@ssw0rd!")
    assert encrypted != "P@ssw0rd!"
    assert secrets.decrypt_secret(encrypted) == "P@ssw0rd!"


def test_secret_masking():
    assert secrets.mask_secret("S3cretValue") == "••••alue"
    assert secrets.mask_secret(None) == "••••"
    assert "P@ss" not in secrets.mask_secret("P@ssw0rd")


def test_redact_log_line():
    line = 'set password "hunter2" for device'
    assert "hunter2" not in secrets.redact_log_line(line)


def test_unauthorized_reboot_denied(client, seeded_device):
    tenant_id = seeded_device["tenant_id"]
    read_only = _headers(tenant_id, "READ_ONLY")
    response = client.post(f"/api/device-management/devices/{seeded_device['device_id']}/actions",
                           json={"action_type": "REBOOT"}, headers=read_only)
    assert response.status_code == 403


def test_factory_reset_requires_elevated_permission(client, seeded_device):
    tenant_id = seeded_device["tenant_id"]
    support = _headers(tenant_id, "SUPPORT_AGENT")
    response = client.post(f"/api/device-management/devices/{seeded_device['device_id']}/actions",
                           json={"action_type": "FACTORY_RESET", "elevated": True}, headers=support)
    # SUPPORT_AGENT lacks device.factory_reset -> 403.
    assert response.status_code == 403


def test_firmware_upload_authorization(client, seeded_device):
    import hashlib

    tenant_id = seeded_device["tenant_id"]
    support = _headers(tenant_id, "SUPPORT_AGENT")
    payload = {"vendor": "FiberHome", "model": "AN5506-04-F1", "version": "V2.0",
               "checksum_sha256": hashlib.sha256(b"\x00" * 32).hexdigest()}
    response = client.post("/api/device-management/firmware", json=payload, headers=support)
    assert response.status_code == 403
    operator = _headers(tenant_id, "FIRMWARE_OPERATOR")
    response = client.post("/api/device-management/firmware", json=payload, headers=operator)
    assert response.status_code == 201


def test_cross_tenant_api_access_denied(client, seeded_device, tenant_b):
    other = _headers(tenant_b, "ISP_ADMIN")
    response = client.get(f"/api/device-management/devices/{seeded_device['device_id']}",
                          params={"tenant_id": str(tenant_b)}, headers=other)
    assert response.status_code == 404


def test_claim_requires_auth(client):
    response = client.post("/api/device-management/devices/00000000-0000-0000-0000-000000000000/claim",
                           json={"method": "ADMIN_CLAIM"})
    assert response.status_code in (401, 403)


def test_connection_request_ssrf_blocked_via_api(client, seeded_device):
    tenant_id = seeded_device["tenant_id"]
    headers = _headers(tenant_id, "DEVICE_OPERATOR")
    response = client.post(f"/api/device-management/devices/{seeded_device['device_id']}/actions",
                           json={"action_type": "CONNECTION_REQUEST",
                                 "parameters": {"connection_request_url": "http://169.254.169.254/latest/"}},
                           headers=headers)
    assert response.status_code == 400
    assert "ssrf" in response.json()["detail"]["code"]
