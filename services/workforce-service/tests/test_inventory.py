"""Inventory: device issuance, spare parts, stock sync (features 337-339)."""
from conftest import make_technician, make_work_order


def _mk_item(client, headers, **over):
    body = {"item_type": "ONT", "serial_number": "SN-1001", "mac_address": "AA:BB:CC:DD:EE:01"}
    body.update(over)
    return client.post("/api/workforce/v1/inventory/items", json=body, headers=headers)


def test_add_and_list_items(client, manager_headers):
    _mk_item(client, manager_headers)
    r = client.get("/api/workforce/v1/inventory/items", headers=manager_headers)
    assert len(r.json()) == 1
    assert r.json()[0]["status"] == "IN_STOCK"


def test_issue_and_return_device(client, manager_headers):
    item = _mk_item(client, manager_headers).json()
    wo = make_work_order(client, manager_headers).json()["id"]
    tid = make_technician(client, manager_headers).json()["id"]
    r = client.post(f"/api/workforce/v1/inventory/items/{item['id']}/issue",
                    headers=manager_headers,
                    json={"work_order_id": wo, "technician_id": tid})
    assert r.json()["status"] == "ISSUED"
    r2 = client.post(f"/api/workforce/v1/inventory/items/{item['id']}/issue",
                     headers=manager_headers, json={"work_order_id": wo})
    assert r2.status_code == 400  # already issued
    r3 = client.post(f"/api/workforce/v1/inventory/items/{item['id']}/return",
                     headers=manager_headers)
    assert r3.json()["status"] == "RETURNED"


def test_consumable_add_and_consume(client, manager_headers):
    client.post("/api/workforce/v1/inventory/consumables", headers=manager_headers,
                json={"name": "Fiber connector", "sku": "FC-01", "quantity": 10})
    wo = make_work_order(client, manager_headers).json()["id"]
    r = client.post("/api/workforce/v1/inventory/consumables/consume",
                    headers=manager_headers,
                    json={"work_order_id": wo, "sku": "FC-01", "quantity": 3})
    assert r.status_code == 200
    r2 = client.post("/api/workforce/v1/inventory/consumables/consume",
                     headers=manager_headers,
                     json={"work_order_id": wo, "sku": "FC-01", "quantity": 99})
    assert r2.status_code == 400  # insufficient stock


def test_inventory_sync(client, manager_headers):
    _mk_item(client, manager_headers)
    r = client.post("/api/workforce/v1/inventory/sync", headers=manager_headers,
                    json={"stock": [{"serial_number": "SN-1001", "status": "DEFECTIVE"}]})
    assert r.json()["reconciled"] == 1
    rl = client.get("/api/workforce/v1/inventory/items", headers=manager_headers)
    assert rl.json()[0]["status"] == "DEFECTIVE"


def test_issue_unknown_item_404(client, manager_headers):
    import uuid
    r = client.post(f"/api/workforce/v1/inventory/items/{uuid.uuid4()}/issue",
                    headers=manager_headers, json={"work_order_id": str(uuid.uuid4())})
    assert r.status_code == 404
