"""HTTP API surface: ACS admin, discovery/claim, profiles, configuration jobs,
actions, diagnostics, firmware, telemetry and reports."""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from conftest import make_token, variant_for
from app.integrations.acs import get_acs_client


@pytest.fixture
def client(defaults):
    with TestClient(app) as c:
        yield c


def _headers(tenant_id, role="ISP_ADMIN"):
    return {"Authorization": f"Bearer {make_token(role, tenant_id)}"}


@pytest.fixture
def acs_instance_id(session, client, auth_headers):
    response = client.post("/api/device-management/acs/instances", json={
        "name": "api-acs", "base_url": "http://genieacs:7557", "environment": "TEST"}, headers=auth_headers)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_health_and_status(client):
    assert client.get("/health").status_code == 200
    assert client.get("/status").json()["service"] == "device-management"


def test_acs_health_check(client, auth_headers, acs_instance_id):
    response = client.post(f"/api/device-management/acs/instances/{acs_instance_id}/health-check",
                           headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["state"] in ("HEALTHY", "UNREACHABLE")


def test_discover_and_claim_via_api(client, auth_headers, acs_instance_id, tenant_id, make_acs_device):
    from app.integrations.fakes import STATE

    STATE.seed_inventory_asset("API-SN1", model="ONT")
    acs_device_id = make_acs_device(serial_number="API-SN1", oui="A4B1C1", product_class="AN5506")
    response = client.post("/api/device-management/devices/discover", json={
        "acs_instance_id": acs_instance_id, "acs_device_id": acs_device_id,
        "tenant_id": str(tenant_id)}, headers=auth_headers)
    assert response.status_code == 201, response.text
    device_id = response.json()["id"]
    response = client.post(f"/api/device-management/devices/{device_id}/claim",
                           json={"method": "PREREGISTERED_SERIAL", "evidence": "API-SN1"},
                           params={"tenant_id": str(tenant_id)}, headers=auth_headers)
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "CLAIMED"


def test_profile_lifecycle_via_api(client, auth_headers, tenant_id):
    headers = auth_headers
    response = client.post("/api/device-management/profiles", json={"code": "API_PROFILE", "name": "API Profile"},
                           headers=headers)
    assert response.status_code == 201
    profile_id = response.json()["id"]
    response = client.post(f"/api/device-management/profiles/{profile_id}/versions", json={
        "definition": {"WIFI_SSID_24GHZ": {"value": "ApiNet", "writable": True}},
        "change_summary": "init"}, headers=headers)
    assert response.status_code == 201
    version_id = response.json()["id"]
    client.post(f"/api/device-management/profiles/versions/{version_id}/submit", headers=headers).raise_for_status()
    client.post(f"/api/device-management/profiles/versions/{version_id}/approve", headers=headers).raise_for_status()
    r = client.post(f"/api/device-management/profiles/versions/{version_id}/activate", headers=headers)
    assert r.status_code == 200 and r.json()["state"] == "ACTIVE"


def test_configuration_job_flow_via_api(client, auth_headers, tenant_id, make_acs_device):
    from app.integrations.fakes import STATE

    STATE.seed_inventory_asset("API-CFG", model="ONT")
    acs_device_id = make_acs_device(serial_number="API-CFG", oui="A4B1C1", product_class="AN5506")
    from app.services import device_service
    from app.models import ACSInstance
    from app.database import SessionLocal

    s = SessionLocal()
    inst = ACSInstance(tenant_id=None, name="cfg-acs-2", base_url="http://genieacs:7557", health="HEALTHY")
    s.add(inst)
    s.commit()
    s.refresh(inst)
    device = device_service.discover_from_acs(s, inst.id, acs_device_id=acs_device_id,
                                              requested_tenant_id=tenant_id, actor="test")
    device_service.claim_device(s, tenant_id, device.id, method="PREREGISTERED_SERIAL",
                                evidence="API-CFG", actor="test")
    s.commit()
    device_id = str(device.id)
    s.close()

    # Create + approve + queue + execute a configuration job via the API.
    r = client.post(f"/api/device-management/devices/{device_id}/configuration-jobs", json={
        "parameters": {"Device.WiFi.SSID.1.SSID": "ApiNet"}, "verification_required": True},
        params={"tenant_id": str(tenant_id)}, headers=auth_headers)
    assert r.status_code == 201, r.text
    job_id = r.json()["id"]
    client.post(f"/api/device-management/configuration-jobs/{job_id}/approve",
                params={"tenant_id": str(tenant_id)}, headers=auth_headers).raise_for_status()
    client.post(f"/api/device-management/configuration-jobs/{job_id}/queue",
                params={"tenant_id": str(tenant_id)}, headers=auth_headers).raise_for_status()
    client.post(f"/api/device-management/configuration-jobs/{job_id}/execute",
                params={"tenant_id": str(tenant_id)}, headers=auth_headers).raise_for_status()

    acs_client = get_acs_client({})
    acs_client.set_device_parameters(acs_device_id, {"Device.WiFi.SSID.1.SSID": "ApiNet"})
    from app.integrations.acs import FakeACSClient

    task_id = FakeACSClient._state["devices"][acs_device_id]["tasks"][-1]
    client.post(f"/api/device-management/configuration-jobs/{job_id}/task-result", json={
        "task_id": task_id, "task_state": "COMPLETED"},
        params={"tenant_id": str(tenant_id)}, headers=auth_headers).raise_for_status()
    r = client.post(f"/api/device-management/configuration-jobs/{job_id}/verify",
                    params={"tenant_id": str(tenant_id)}, headers=auth_headers)
    assert r.status_code == 200 and r.json()["state"] == "SUCCEEDED"


def test_diagnostics_via_api(client, auth_headers, tenant_id, make_acs_device):
    from app.integrations.fakes import STATE
    from app.services import device_service
    from app.models import ACSInstance
    from app.database import SessionLocal

    STATE.seed_inventory_asset("API-DIAG", model="ONT")
    acs_device_id = make_acs_device(serial_number="API-DIAG", oui="A4B1C1", product_class="AN5506")
    s = SessionLocal()
    inst = ACSInstance(tenant_id=None, name="diag-acs", base_url="http://genieacs:7557", health="HEALTHY")
    s.add(inst)
    s.commit()
    s.refresh(inst)
    device = device_service.discover_from_acs(s, inst.id, acs_device_id=acs_device_id,
                                              requested_tenant_id=tenant_id, actor="test")
    device_service.claim_device(s, tenant_id, device.id, method="PREREGISTERED_SERIAL",
                                evidence="API-DIAG", actor="test")
    device.model_variant_id = variant_for(s, model_name="AN5506-04-F1").id
    s.commit()
    device_id = str(device.id)
    s.close()
    r = client.post(f"/api/device-management/devices/{device_id}/diagnostics", json={
        "diagnostic_type": "PING", "requested_by": "support"}, params={"tenant_id": str(tenant_id)},
        headers=auth_headers)
    assert r.status_code == 201, r.text
    job_id = r.json()["id"]
    client.post(f"/api/device-management/diagnostics/{job_id}/run", params={"tenant_id": str(tenant_id)},
                headers=auth_headers).raise_for_status()
    r = client.post(f"/api/device-management/diagnostics/{job_id}/result", json={
        "raw": {"success": True, "average_rtt_ms": 3}},
        params={"tenant_id": str(tenant_id)}, headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["state"] == "SUCCEEDED"


def test_firmware_rollout_via_api(client, auth_headers, tenant_id, make_acs_device):
    from app.integrations.fakes import STATE
    from app.services import device_service
    from app.models import ACSInstance
    from app.database import SessionLocal

    STATE.seed_inventory_asset("API-FW", model="ONT")
    acs_device_id = make_acs_device(serial_number="API-FW", oui="A4B1C1", product_class="AN5506")
    s = SessionLocal()
    inst = ACSInstance(tenant_id=None, name="fw-acs", base_url="http://genieacs:7557", health="HEALTHY")
    s.add(inst)
    s.commit()
    s.refresh(inst)
    device = device_service.discover_from_acs(s, inst.id, acs_device_id=acs_device_id,
                                              requested_tenant_id=tenant_id, actor="test")
    device_service.claim_device(s, tenant_id, device.id, method="PREREGISTERED_SERIAL",
                                evidence="API-FW", actor="test")
    variant = variant_for(s, model_name="AN5506-04-F1")
    device.model_variant_id = variant.id
    device.firmware_version = "V1.0"
    s.commit()
    device_id = str(device.id)
    s.close()

    import hashlib

    checksum = hashlib.sha256(b"\x00" * 32).hexdigest()
    r = client.post("/api/device-management/firmware", json={
        "vendor": "FiberHome", "model": "AN5506-04-F1", "version": "V2.0", "checksum_sha256": checksum},
        headers=auth_headers)
    assert r.status_code == 201, r.text
    artifact_id = r.json()["id"]
    client.post(f"/api/device-management/firmware/{artifact_id}/approve", json={"decision": "APPROVED"},
                headers=auth_headers).raise_for_status()
    r = client.post("/api/device-management/firmware/rollouts", json={
        "artifact_id": artifact_id, "name": "api-rollout", "strategy": "LAB",
        "policy": {"stage_size": 1, "success_threshold": 0.95, "failure_threshold": 0.1}},
        headers=auth_headers)
    assert r.status_code == 201, r.text
    rollout_id = r.json()["id"]
    client.post(f"/api/device-management/firmware/rollouts/{rollout_id}/start",
                headers=auth_headers).raise_for_status()
    r = client.post(f"/api/device-management/firmware/rollouts/{rollout_id}/deployments", json={
        "cpe_id": device_id}, headers=auth_headers)
    assert r.status_code == 201, r.text
    deployment_id = r.json()["id"]
    client.post(f"/api/device-management/firmware/deployments/{deployment_id}/execute",
                headers=auth_headers).raise_for_status()
    acs_client = get_acs_client({})
    acs_client.set_device_parameters(acs_device_id, {"Device.DeviceInfo.SoftwareVersion": "V2.0"})
    r = client.post(f"/api/device-management/firmware/deployments/{deployment_id}/outcome", json={
        "reported_firmware": "V2.0", "health_checks": {"ping": True}}, headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["state"] == "SUCCEEDED"


def test_reports_and_audit(client, auth_headers, tenant_id, make_acs_device):
    from app.integrations.fakes import STATE
    from app.services import device_service
    from app.models import ACSInstance
    from app.database import SessionLocal

    STATE.seed_inventory_asset("API-REP", model="ONT")
    make_acs_device(serial_number="API-REP", oui="A4B1C1", product_class="AN5506", device_id="dev-API-REP")
    s = SessionLocal()
    inst = ACSInstance(tenant_id=None, name="rep-acs", base_url="http://genieacs:7557", health="HEALTHY")
    s.add(inst)
    s.commit()
    s.refresh(inst)
    device = device_service.discover_from_acs(s, inst.id, acs_device_id="dev-API-REP",
                                              requested_tenant_id=tenant_id, actor="test")
    device_service.claim_device(s, tenant_id, device.id, method="ADMIN_CLAIM", actor="test")
    s.commit()
    s.close()
    overview = client.get("/api/device-management/reports/overview",
                          params={"tenant_id": str(tenant_id)}, headers=auth_headers).json()
    assert overview["managed_devices"] >= 1
    by_state = client.get("/api/device-management/reports/devices",
                          params={"tenant_id": str(tenant_id)}, headers=auth_headers).json()
    assert "by_state" in by_state
    audit = client.get("/api/device-management/audit", params={"tenant_id": str(tenant_id)},
                       headers=auth_headers)
    assert audit.status_code == 200
