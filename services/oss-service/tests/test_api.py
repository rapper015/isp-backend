"""HTTP API tests for the OSS service (Milestone 2 endpoints under /api/oss)."""
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_health_and_status(client):
    assert client.get("/health").json()["status"] == "ok"
    status = client.get("/status").json()
    assert status["service"] == "oss"
    assert "milestone-2" in status["phase"]


def test_create_and_get_order(client, tenant_id, auth_headers):
    payload = {
        "tenant_id": str(tenant_id),
        "order_type": "NEW_CONNECTION",
        "customer_id": "cust-valid-001",
        "service_location_id": "loc-1",
        "requested_plan_reference": "plan-fiber-100",
    }
    r = client.post("/api/oss/orders", json=payload, headers=auth_headers)
    assert r.status_code == 201
    body = r.json()
    assert body["state"] == "DRAFT"
    assert body["order_number"].startswith("ORD-")
    order_id = body["id"]
    r = client.get(f"/api/oss/orders/{order_id}", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["state"] == "DRAFT"


def test_management_auth_required(client, tenant_id):
    payload = {"tenant_id": str(tenant_id), "order_type": "NEW_CONNECTION", "customer_id": "cust-valid-001"}
    r = client.post("/api/oss/orders", json=payload)
    assert r.status_code == 401


def test_submit_validate_fulfil_flow(client, tenant_id, auth_headers, seeded_resources):
    payload = {
        "tenant_id": str(tenant_id),
        "order_type": "NEW_CONNECTION",
        "customer_id": "cust-valid-001",
        "service_location_id": "loc-1",
        "requested_plan_reference": "plan-fiber-100",
        "priority": "HIGH",
        "requested_snapshot": {"ont_serial": "ONT-SN-1001", "nas_reference": "nas-test", "pop": "pop-1", "node": "node-1"},
    }
    r = client.post("/api/oss/orders", json=payload, headers=auth_headers)
    order_id = r.json()["id"]

    r = client.post(f"/api/oss/orders/{order_id}/submit", json={}, headers=auth_headers)
    assert r.json()["state"] == "SUBMITTED"

    r = client.post(f"/api/oss/orders/{order_id}/validate", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["result_state"] == "READY_FOR_FULFILMENT"

    r = client.post(f"/api/oss/orders/{order_id}/fulfil", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["state"] == "COMPLETED"

    r = client.get(f"/api/oss/orders/{order_id}/events", headers=auth_headers)
    assert r.status_code == 200
    events = r.json()
    assert events[0]["event_type"] == "oss.order.created.v1"
    assert [e["aggregate_version"] for e in events] == list(range(1, len(events) + 1))

    r = client.get(f"/api/oss/orders/{order_id}/history", headers=auth_headers)
    assert len(r.json()) >= 2

    r = client.get(f"/api/oss/orders/{order_id}/valid-actions", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["state"] == "COMPLETED"
    assert r.json()["valid_actions"] == []


def test_resources_register_and_capacity(client, tenant_id, auth_headers):
    r = client.post("/api/oss/resources/register", json={"tenant_id": str(tenant_id), "resource_type": "IPV4", "resource_key": "192.168.50.10"}, headers=auth_headers)
    assert r.status_code == 201
    r = client.get("/api/oss/resources/capacity", params={"tenant_id": str(tenant_id)}, headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["capacity"]["IPV4"]["AVAILABLE"] == 1


def test_subscriptions_endpoint(client, tenant_id, auth_headers, seeded_resources):
    payload = {
        "tenant_id": str(tenant_id),
        "order_type": "NEW_CONNECTION",
        "customer_id": "cust-valid-001",
        "service_location_id": "loc-1",
        "requested_plan_reference": "plan-fiber-100",
        "requested_snapshot": {"ont_serial": "ONT-SN-1001", "nas_reference": "nas-test", "pop": "pop-1", "node": "node-1"},
    }
    order_id = client.post("/api/oss/orders", json=payload, headers=auth_headers).json()["id"]
    client.post(f"/api/oss/orders/{order_id}/submit", json={}, headers=auth_headers)
    client.post(f"/api/oss/orders/{order_id}/validate", headers=auth_headers)
    client.post(f"/api/oss/orders/{order_id}/fulfil", headers=auth_headers)
    r = client.get("/api/oss/subscriptions", params={"tenant_id": str(tenant_id)}, headers=auth_headers)
    subs = r.json()
    assert len(subs) == 1
    assert subs[0]["status"] == "ACTIVE"
