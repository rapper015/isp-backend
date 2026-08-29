"""HTTP API surface: management work orders, technician mobile, dispatch, QA,
field SLA, customer portal, reports and audit — plus auth enforcement."""
import os
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import app
from conftest import make_customer_token, make_technician_token, make_token

TODAY = datetime.now(timezone.utc).replace(hour=10, minute=0, second=0, microsecond=0)


@pytest.fixture
def client(defaults):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def headers(tenant_id):
    return _headers(tenant_id)


@pytest.fixture
def wo_payload(tenant_id):
    return {
        "tenant_id": str(tenant_id),
        "work_order_type": "NEW_INSTALLATION",
        "customer_id": "CUST-0001",
        "customer_name": "Test Customer",
        "service_subscription_id": "SUB-0001",
        "service_location_id": "loc-1",
        "latitude": 28.6139,
        "longitude": 77.2090,
        "priority": "P3_MEDIUM",
        "severity": "SEV3",
        "source_channel": "API",
    }


def _headers(tenant_id, role="ISP_ADMIN"):
    return {"Authorization": f"Bearer {make_token(role, tenant_id)}"}


def _create_wo(client, headers, wo_payload, **overrides):
    payload = {**wo_payload, **overrides}
    response = client.post("/api/workforce/work-orders", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def _lifecycle_to_dispatch(client, headers, wo_payload, technician_id):
    wo = _create_wo(client, headers, wo_payload)
    wid = wo["id"]
    client.post(f"/api/workforce/work-orders/{wid}/validate", headers=headers).raise_for_status()
    r = client.post(f"/api/workforce/work-orders/{wid}/schedule", json={
        "window_start": (TODAY + timedelta(days=1)).isoformat(),
        "window_end": (TODAY + timedelta(days=1, hours=2)).isoformat(),
    }, headers=headers)
    assert r.status_code == 200, r.text
    r = client.post(f"/api/workforce/work-orders/{wid}/assign", json={
        "technician_id": technician_id, "reason": "api assignment"}, headers=headers)
    assert r.status_code == 200, r.text
    r = client.post(f"/api/workforce/work-orders/{wid}/dispatch", headers=headers)
    assert r.status_code == 200, r.text
    return wo


def test_health_and_status(client):
    assert client.get("/health").status_code == 200
    assert client.get("/status").json()["service"] == "workforce"


def test_create_work_order_requires_auth(client, wo_payload):
    response = client.post("/api/workforce/work-orders", json=wo_payload)
    assert response.status_code in (401, 403)


def test_create_work_order_api(client, headers, wo_payload):
    wo = _create_wo(client, headers, wo_payload)
    assert wo["work_order_number"].startswith("WO-")
    assert wo["status"] == "CREATED"


def test_create_rejects_bad_type(client, headers, wo_payload):
    response = client.post("/api/workforce/work-orders", json={**wo_payload, "work_order_type": "BOGUS"},
                           headers=headers)
    assert response.status_code in (400, 422)


def test_insufficient_role_denied(client, wo_payload, tenant_id):
    read_only = {"Authorization": f"Bearer {make_token('READ_ONLY', tenant_id)}"}
    response = client.post("/api/workforce/work-orders", json=wo_payload, headers=read_only)
    assert response.status_code == 403


def test_list_filters_by_status(client, headers, wo_payload):
    _create_wo(client, headers, wo_payload)
    response = client.get("/api/workforce/work-orders", params={"status": "CREATED"}, headers=headers)
    assert response.status_code == 200
    assert all(wo["status"] == "CREATED" for wo in response.json())


def test_validate_schedule_assign_dispatch(client, headers, wo_payload, tenant_id, make_technician, session):
    tech = make_technician("API Tech", skills=["FIBER_INSTALL", "ONT_INSTALL"],
                           certifications=[{"certification": "FIBER_SAFETY"}])
    wo = _lifecycle_to_dispatch(client, headers, wo_payload, str(tech.id))
    wid = wo["id"]
    detail = client.get(f"/api/workforce/work-orders/{wid}", headers=headers).json()
    assert detail["status"] == "DISPATCHED"
    assert detail["assigned_technician_id"] == str(tech.id)
    assert len(detail["events"]) >= 5


def test_technician_mobile_flow(client, headers, wo_payload, tenant_id, make_technician):
    tech = make_technician("Mobile Tech", skills=["FIBER_INSTALL", "ONT_INSTALL"],
                           certifications=[{"certification": "FIBER_SAFETY"}])
    wo = _lifecycle_to_dispatch(client, headers, wo_payload, str(tech.id))
    tech_headers = {"Authorization": f"Bearer {make_technician_token(str(tech.id), tenant_id)}"}
    wid = wo["id"]

    me = client.get("/api/workforce/technician/me", headers=tech_headers)
    assert me.status_code == 200 and me.json()["technician_id"] == str(tech.id)

    assignments = client.get("/api/workforce/technician/assignments", headers=tech_headers).json()
    assert any(a["id"] == wid for a in assignments)

    r = client.post(f"/api/workforce/technician/assignments/{wid}/accept", headers=tech_headers)
    assert r.status_code == 200, r.text
    r = client.post(f"/api/workforce/technician/assignments/{wid}/start-travel", headers=tech_headers)
    assert r.status_code == 200, r.text
    r = client.post(f"/api/workforce/technician/assignments/{wid}/check-in", json={
        "latitude": 28.6139, "longitude": 77.2090, "gps_accuracy_m": 15}, headers=tech_headers)
    assert r.status_code == 200, r.text
    r = client.post(f"/api/workforce/technician/assignments/{wid}/start-work", headers=tech_headers)
    assert r.status_code == 200, r.text
    assert client.get(f"/api/workforce/work-orders/{wid}", headers=headers).json()["status"] == "IN_PROGRESS"


def test_technician_cannot_access_others_work(client, headers, wo_payload, tenant_id, make_technician):
    tech = make_technician("Other Tech", skills=["FIBER_INSTALL", "ONT_INSTALL"],
                           certifications=[{"certification": "FIBER_SAFETY"}])
    wo = _create_wo(client, headers, wo_payload)
    tech_headers = {"Authorization": f"Bearer {make_technician_token(str(tech.id), tenant_id)}"}
    response = client.post(f"/api/workforce/technician/assignments/{wo['id']}/accept", headers=tech_headers)
    assert response.status_code == 403


def test_dispatch_endpoints(client, headers, wo_payload, tenant_id, make_technician):
    tech = make_technician("Dispatch Tech", skills=["FIBER_INSTALL", "ONT_INSTALL"],
                           certifications=[{"certification": "FIBER_SAFETY"}])
    _lifecycle_to_dispatch(client, headers, wo_payload, str(tech.id))
    unassigned = client.get("/api/workforce/dispatch/unassigned", headers=headers).json()
    assert isinstance(unassigned, list)
    board = client.get("/api/workforce/dispatch/board", headers=headers)
    assert board.status_code == 200


def test_dispatch_recommendations(client, headers, wo_payload, tenant_id, make_technician):
    tech = make_technician("Rec Tech", skills=["FIBER_INSTALL", "ONT_INSTALL"],
                           certifications=[{"certification": "FIBER_SAFETY"}])
    wo = _create_wo(client, headers, wo_payload)
    r = client.get(f"/api/workforce/dispatch/recommendations/{wo['id']}", headers=headers)
    assert r.status_code == 200
    recs = r.json()["recommendations"]
    assert len(recs) >= 1
    assert any(str(x["technician_id"]) == str(tech.id) for x in recs)


def test_qa_approve_flow(client, headers, wo_payload, tenant_id, make_technician):
    tech = make_technician("QA Flow Tech", skills=["FIBER_INSTALL", "ONT_INSTALL"],
                           certifications=[{"certification": "FIBER_SAFETY"}])
    wo = _lifecycle_to_dispatch(client, headers, wo_payload, str(tech.id))
    wid = wo["id"]
    tech_headers = {"Authorization": f"Bearer {make_technician_token(str(tech.id), tenant_id)}"}
    # Drive execution via technician endpoints.
    checklist = {
        "VERIFY_CUSTOMER": True, "INSPECT_SITE": True, "INSTALL_CABLE": True, "INSTALL_ONT": True,
        "SCAN_SERIAL": "ONT-SN-9001", "RECORD_MAC": "AA:BB:CC:DD:EE:90", "OPTICAL_READING": -18.0,
        "SERVICE_TEST": 100, "PHOTO_INSTALLATION": {"file_ref": "api-photo-1"},
        "CUSTOMER_ACK": {"file_ref": "api-ack"}, "MATERIALS_USED": "fiber",
    }
    for cmd in ("accept", "start-travel"):
        client.post(f"/api/workforce/technician/assignments/{wid}/{cmd}", headers=tech_headers).raise_for_status()
    r = client.post(f"/api/workforce/technician/assignments/{wid}/check-in", json={
        "latitude": 28.6139, "longitude": 77.2090, "gps_accuracy_m": 15}, headers=tech_headers)
    assert r.status_code == 200, r.text
    client.post(f"/api/workforce/technician/assignments/{wid}/start-work", headers=tech_headers).raise_for_status()
    r = client.post(f"/api/workforce/technician/assignments/{wid}/checklist", json={"responses": checklist},
                    headers=tech_headers)
    assert r.status_code == 200, r.text
    for code in ("PHOTOGRAPH", "SERIAL_NUMBER", "CUSTOMER_ACKNOWLEDGEMENT"):
        client.post(f"/api/workforce/technician/assignments/{wid}/proof", json={
            "evidence_key": f"api-proof-{code}", "evidence_type": code, "file_ref": "api-file",
            "capture_timestamp": TODAY.isoformat()}, headers=tech_headers).raise_for_status()
    client.post(f"/api/workforce/technician/assignments/{wid}/acknowledgement", json={
        "method": "CUSTOMER_SIGNATURE", "masked_recipient": "cus***01", "result": "CONFIRMED"}, headers=tech_headers).raise_for_status()
    for mat in ("FIBER_CONNECTOR", "SPLICE"):
        client.post(f"/api/workforce/technician/assignments/{wid}/materials", json={
            "material_code": mat, "quantity": 1}, headers=tech_headers).raise_for_status()
    client.post(f"/api/workforce/technician/assignments/{wid}/devices", json={
        "device_type": "ONT", "serial_number": "ONT-SN-9001", "mac_address": "AA:BB:CC:DD:EE:90"},
        headers=tech_headers).raise_for_status()
    r = client.post(f"/api/workforce/technician/assignments/{wid}/finish", headers=tech_headers)
    assert r.status_code == 200, r.text
    r = client.post(f"/api/workforce/technician/assignments/{wid}/verify", headers=tech_headers)
    assert r.status_code == 200, r.text

    pending = client.get("/api/workforce/qa/pending", headers=headers).json()
    assert any(p["work_order_id"] == wid for p in pending)

    r = client.post(f"/api/workforce/qa/{wid}/approve", json={"reason": "looks good"}, headers=headers)
    assert r.status_code == 200, r.text
    assert client.get(f"/api/workforce/work-orders/{wid}", headers=headers).json()["status"] == "COMPLETED"


def test_customer_portal(client, headers, wo_payload, tenant_id, make_technician, session):
    tech = make_technician("Portal Tech", skills=["FIBER_INSTALL", "ONT_INSTALL"],
                           certifications=[{"certification": "FIBER_SAFETY"}])
    wo = _lifecycle_to_dispatch(client, headers, wo_payload, str(tech.id))
    customer_headers = {"Authorization": f"Bearer {make_customer_token('CUST-0001', tenant_id)}"}

    appointments = client.get("/api/workforce/portal/appointments", headers=customer_headers).json()
    assert len(appointments) >= 1

    status = client.get(f"/api/workforce/portal/work-orders/{wo['id']}", headers=customer_headers)
    assert status.status_code == 200
    body = status.json()
    assert body["work_order_number"].startswith("WO-")
    # Privacy-safe: no exact technician coordinates, no internal notes.
    assert "assigned_technician_name" not in body


def test_reports_and_audit(client, headers, wo_payload):
    _create_wo(client, headers, wo_payload)
    overview = client.get("/api/workforce/reports/overview", headers=headers).json()
    assert overview["open_work_orders"] >= 1
    by_status = client.get("/api/workforce/reports/tickets", headers=headers).json()
    assert by_status["by_status"]["CREATED"] >= 1
    audit = client.get("/api/workforce/audit", headers=headers)
    assert audit.status_code == 200


def test_field_sla_policy_api(client, headers, tenant_id):
    r = client.post("/api/workforce/sla/policies", json={"code": "FIELD_API", "name": "API Policy"}, headers=headers)
    assert r.status_code == 201, r.text
    policy_id = r.json()["id"]
    r = client.post(f"/api/workforce/sla/policies/{policy_id}/versions", json={
        "definition": {"pause_on_states": ["AWAITING_PARTS"], "escalation": []},
        "targets": [{"kind": "ARRIVAL", "business_seconds": 3600, "priority": "ALL"},
                    {"kind": "TIME_TO_COMPLETE", "business_seconds": 10800, "priority": "ALL"}],
        "activate": True,
    }, headers=headers)
    assert r.status_code == 201, r.text
    assert r.json()["active"] is True
