from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app


HEADERS = {"X-CRM-Service-Key": "test-internal-key"}


def test_created_tenants_are_available_from_directory():
    first_name = f"directory-a-{uuid4().hex}"
    second_name = f"directory-b-{uuid4().hex}"

    with TestClient(app) as client:
        first = client.post("/api/crm/tenants", json={"name": first_name}, headers=HEADERS)
        second = client.post("/api/crm/tenants", json={"name": second_name}, headers=HEADERS)

        assert first.status_code == 200
        assert second.status_code == 200

        response = client.get("/api/crm/tenants?limit=100", headers=HEADERS)
        assert response.status_code == 200
        directory = {tenant["id"]: tenant for tenant in response.json()}
        assert directory[first.json()["id"]]["name"] == first_name
        assert directory[second.json()["id"]]["name"] == second_name
        assert directory[first.json()["id"]]["enabled"] is True
