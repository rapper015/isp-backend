"""Work-order lifecycle, dispatch, technician state (features 329-330, 333-334)."""
from conftest import make_technician, make_work_order

import uuid


def test_create_work_order(client, manager_headers):
    r = make_work_order(client, manager_headers)
    assert r.status_code == 201
    body = r.json()
    assert body["ref_id"].startswith("WO-")
    assert body["status"] == "CREATED"
    assert body["sla_deadline"] is not None


def test_work_order_numbering_increments(client, manager_headers):
    a = make_work_order(client, manager_headers).json()
    b = make_work_order(client, manager_headers).json()
    assert a["ref_id"] != b["ref_id"]


def test_full_lifecycle(client, manager_headers):
    wo = make_work_order(client, manager_headers).json()
    wid = wo["id"]
    tid = make_technician(client, manager_headers).json()["id"]
    client.post(f"/api/workforce/v1/work-orders/{wid}/assign", headers=manager_headers,
                json={"technician_id": tid})
    client.post(f"/api/workforce/v1/work-orders/{wid}/dispatch", headers=manager_headers, json={})
    for tr in ("EN_ROUTE", "ARRIVED", "IN_PROGRESS"):
        r = client.post(f"/api/workforce/v1/work-orders/{wid}/transition",
                        headers=manager_headers, json={"transition": tr})
        assert r.status_code == 200, r.text
    r = client.post(f"/api/workforce/v1/work-orders/{wid}/complete",
                    headers=manager_headers, json={"note": "done"})
    assert r.json()["status"] == "COMPLETED"
    assert r.json()["completed_at"] is not None


def test_invalid_transition_rejected(client, manager_headers):
    wo = make_work_order(client, manager_headers).json()
    r = client.post(f"/api/workforce/v1/work-orders/{wo['id']}/transition",
                    headers=manager_headers, json={"transition": "COMPLETED"})
    assert r.status_code == 400  # CREATED -> COMPLETED not allowed


def test_assign_and_dispatch(client, manager_headers):
    wo = make_work_order(client, manager_headers).json()
    tid = make_technician(client, manager_headers).json()["id"]
    r = client.post(f"/api/workforce/v1/work-orders/{wo['id']}/assign",
                    headers=manager_headers, json={"technician_id": tid})
    assert r.json()["status"] == "ASSIGNED"
    assert r.json()["technician_id"] == tid
    r2 = client.post(f"/api/workforce/v1/work-orders/{wo['id']}/dispatch",
                     headers=manager_headers, json={"notes": "go"})
    assert r2.json()["status"] == "DISPATCHED"


def test_dispatch_requires_assignment(client, manager_headers):
    wo = make_work_order(client, manager_headers).json()
    r = client.post(f"/api/workforce/v1/work-orders/{wo['id']}/dispatch",
                    headers=manager_headers, json={})
    assert r.status_code == 400  # no technician assigned


def test_assign_unknown_technician_404(client, manager_headers):
    wo = make_work_order(client, manager_headers).json()
    r = client.post(f"/api/workforce/v1/work-orders/{wo['id']}/assign",
                    headers=manager_headers, json={"technician_id": str(uuid.uuid4())})
    assert r.status_code == 404


def test_dispatch_suggest_available_only(client, manager_headers):
    make_technician(client, manager_headers, name="A", skills=["FTTx"])
    make_technician(client, manager_headers, name="B", skills=["WiFi"])
    wo = make_work_order(client, manager_headers).json()
    r = client.get(f"/api/workforce/v1/dispatch/suggest?work_order_id={wo['id']}&skills=FTTx",
                   headers=manager_headers)
    names = [t["name"] for t in r.json()]
    assert "A" in names
    assert "B" not in names


def test_technician_status_transitions(client, manager_headers):
    tid = make_technician(client, manager_headers).json()["id"]
    r = client.post(f"/api/workforce/v1/technicians/{tid}/status",
                    headers=manager_headers, json={"status": "ON_LEAVE"})
    assert r.json()["status"] == "ON_LEAVE"
    r2 = client.post(f"/api/workforce/v1/technicians/{tid}/status",
                     headers=manager_headers, json={"status": "NOPE"})
    assert r2.status_code == 400


def test_list_and_filter_work_orders(client, manager_headers):
    make_work_order(client, manager_headers, type="REPAIR")
    make_work_order(client, manager_headers, type="INSTALLATION")
    r = client.get("/api/workforce/v1/work-orders?type=REPAIR", headers=manager_headers)
    assert len(r.json()) == 1
