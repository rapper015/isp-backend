from uuid import uuid4
from fastapi.testclient import TestClient
from app.main import app


def test_logical_radius_group_is_tenant_scoped_and_cannot_be_removed_while_assigned():
    headers = {"X-AAA-Service-Key": "test-internal-key"}
    with TestClient(app) as client:
        tenant_id = client.post("/api/aaa/tenants", json={"name": f"radius-group-{uuid4().hex}"}, headers=headers).json()["id"]
        group = client.post("/api/aaa/radius-server-groups", json={"name": f"west-{uuid4().hex}", "tenant_id": tenant_id, "minimum_healthy": 2}, headers=headers)
        assert group.status_code == 200
        group_id = group.json()["id"]
        nas = client.post("/api/aaa/nas", json={"tenant_id": tenant_id, "name": "edge", "source_ip": "198.51.100.101"}, headers=headers).json()["id"]
        assert client.patch(f"/api/aaa/nas/{nas}?tenant_id={tenant_id}", json={"radius_group_id": group_id}, headers=headers).status_code == 200
        assert client.delete(f"/api/aaa/radius-server-groups/{group_id}", headers=headers).status_code == 409
