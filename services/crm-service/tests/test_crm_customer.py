"""Integration tests: customers, contacts, addresses, lifecycle, risk, merge
and tenant isolation."""
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app

HEADERS = {"X-CRM-Service-Key": "test-internal-key"}


def _tenant(client):
    return client.post("/api/crm/tenants", json={"name": f"tenant-{uuid4().hex}"}, headers=HEADERS).json()["id"]


def _customer(client, tenant_id):
    return client.post(f"/api/crm/customers?tenant_id={tenant_id}", json={"full_name": "Jane Smith", "phone": f"9{uuid4().int % 100000000:08d}", "email": f"jane{uuid4().int}@example.com", "customer_type": "INDIVIDUAL"}, headers=HEADERS).json()


def test_customer_contacts_and_addresses():
    with TestClient(app) as client:
        tenant_id = _tenant(client)
        customer = _customer(client, tenant_id)
        customer_id = customer["id"]
        contact = client.post(f"/api/crm/customers/{customer_id}/contacts?tenant_id={tenant_id}", json={"role": "CONTACT_PERSON", "mobile": f"9{uuid4().int % 100000000:08d}", "is_primary": True}, headers=HEADERS)
        assert contact.status_code == 200
        verified = client.post(f"/api/crm/customers/{customer_id}/contacts/{contact.json()['id']}/verify?tenant_id={tenant_id}", headers=HEADERS)
        assert verified.json()["verification_state"] == "VERIFIED"
        address = client.post(f"/api/crm/customers/{customer_id}/addresses?tenant_id={tenant_id}", json={"address_type": "INSTALLATION", "city": "Pune", "state": "MH", "zipcode": "411001", "latitude": 18.5, "longitude": 73.8}, headers=HEADERS)
        assert address.status_code == 200
        updated = client.patch(f"/api/crm/customers/{customer_id}/addresses/{address.json()['id']}?tenant_id={tenant_id}", json={"address_type": "INSTALLATION", "city": "Mumbai", "state": "MH"}, headers=HEADERS)
        assert updated.json()["version"] == 2
        history = client.get(f"/api/crm/customers/{customer_id}/addresses/history?tenant_id={tenant_id}", headers=HEADERS).json()
        assert len(history) == 2  # versioned history preserved


def test_lifecycle_transitions_and_risk():
    with TestClient(app) as client:
        tenant_id = _tenant(client)
        customer = _customer(client, tenant_id)
        customer_id = customer["id"]
        transitioned = client.post(f"/api/crm/customers/{customer_id}/transition?tenant_id={tenant_id}", json={"to_state": "ONBOARDING", "trigger": "manual"}, headers=HEADERS)
        assert transitioned.json()["lifecycle_state"] == "ONBOARDING"
        bad = client.post(f"/api/crm/customers/{customer_id}/transition?tenant_id={tenant_id}", json={"to_state": "ACTIVE", "trigger": "manual"}, headers=HEADERS)
        assert bad.status_code == 422  # invalid direct jump
        risk = client.post(f"/api/crm/customers/{customer_id}/risk?tenant_id={tenant_id}", json={"level": "HIGH", "source": "BSS_PAYMENT", "reason": "overdue"}, headers=HEADERS)
        assert risk.json()["level"] == "HIGH"
        override = client.post(f"/api/crm/customers/{customer_id}/risk/override?tenant_id={tenant_id}", json={"level": "LOW", "reason": "resolved", "expires_in_seconds": 3600}, headers=HEADERS)
        assert override.status_code == 200


def test_merge_preserves_child_records():
    with TestClient(app) as client:
        tenant_id = _tenant(client)
        primary = _customer(client, tenant_id)
        duplicate = _customer(client, tenant_id)
        duplicate_id = duplicate["id"]
        client.post(f"/api/crm/customers/{duplicate_id}/contacts?tenant_id={tenant_id}", json={"role": "BILLING", "mobile": f"9{uuid4().int % 100000000:08d}"}, headers=HEADERS)
        preview = client.post(f"/api/crm/customers/{primary['id']}/merge-preview?tenant_id={tenant_id}", json={"duplicate_id": duplicate_id}, headers=HEADERS)
        assert preview.json()["contacts_to_move"] == 1
        merged = client.post(f"/api/crm/customers/{primary['id']}/merge?tenant_id={tenant_id}", json={"duplicate_id": duplicate_id}, headers=HEADERS)
        assert merged.status_code == 200


def test_tenant_isolation():
    with TestClient(app) as client:
        tenant_a = _tenant(client)
        tenant_b = _tenant(client)
        customer = _customer(client, tenant_a)
        # Tenant B cannot read Tenant A's customer.
        assert client.get(f"/api/crm/customers/{customer['id']}?tenant_id={tenant_b}", headers=HEADERS).status_code == 404
        assert client.post(f"/api/crm/customers/{customer['id']}/risk?tenant_id={tenant_b}", json={"level": "HIGH", "source": "MANUAL_REVIEW", "reason": "x"}, headers=HEADERS).status_code == 404
        # List only returns own tenant's customers.
        listing = client.get(f"/api/crm/customers?tenant_id={tenant_b}", headers=HEADERS).json()
        assert all(item["id"] != customer["id"] for item in listing)


def test_milestone0_legacy_endpoints_preserved():
    with TestClient(app) as client:
        created = client.post("/customers", json={"full_name": "Legacy User", "phone": f"9{uuid4().int % 100000000:08d}", "email": f"legacy{uuid4().int}@example.com"}, headers=HEADERS)
        assert created.status_code == 201
        code = created.json()["customer_code"]
        by_code = client.get(f"/customers/by-code/{code}", headers=HEADERS)
        assert by_code.status_code == 200
        assert by_code.json()["full_name"] == "Legacy User"


def test_legacy_routes_require_auth():
    """Legacy root-path routes must be protected like the /api/crm surface."""
    with TestClient(app) as client:
        # no service key -> 401
        assert client.get("/customers").status_code == 401
        assert client.get("/leads").status_code == 401
        assert client.post("/customers", json={"full_name": "X", "phone": "9000000000"}).status_code == 401
        assert client.post("/leads", json={"primary_mobile": "9000000000"}).status_code == 401
        assert client.post("/franchises", json={"franchise_code": "F", "name": "F"}).status_code == 401
        assert client.post("/branches", json={"franchise_id": str(uuid4()), "branch_code": "B", "name": "B"}).status_code == 401
        # with service key -> allowed
        assert client.get("/customers", headers=HEADERS).status_code == 200
        assert client.get("/leads", headers=HEADERS).status_code == 200
