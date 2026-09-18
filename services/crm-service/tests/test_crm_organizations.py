from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app


HEADERS = {"X-CRM-Service-Key": "test-internal-key"}


def create_tenant(client: TestClient) -> str:
    response = client.post("/api/crm/tenants", json={"name": f"tenant-{uuid4().hex}"}, headers=HEADERS)
    assert response.status_code == 200
    return response.json()["id"]


def test_franchise_and_branch_reads_are_tenant_scoped():
    with TestClient(app) as client:
        tenant_a = create_tenant(client)
        tenant_b = create_tenant(client)

        franchise = client.post(
            "/api/crm/franchises",
            params={"tenant_id": tenant_a},
            json={"franchise_code": "F-001", "name": "North Franchise", "profile": {"gstin": "22ABCDE1234F1Z5", "enable_sms": True}},
            headers=HEADERS,
        ).json()
        client.post(
            "/api/crm/franchises",
            params={"tenant_id": tenant_b},
            json={"franchise_code": "F-OTHER", "name": "Other Franchise"},
            headers=HEADERS,
        )
        branch = client.post(
            "/api/crm/branches",
            params={"tenant_id": tenant_a},
            json={"franchise_id": franchise["id"], "branch_code": "B-001", "name": "Central Branch"},
            headers=HEADERS,
        ).json()

        franchises = client.get("/api/crm/franchises", params={"tenant_id": tenant_a}, headers=HEADERS)
        assert franchises.status_code == 200
        assert [(row["franchise_code"], row["name"]) for row in franchises.json()] == [("F-001", "North Franchise")]

        franchise_detail = client.get(f"/api/crm/franchises/{franchise['id']}", params={"tenant_id": tenant_a}, headers=HEADERS)
        assert franchise_detail.status_code == 200
        assert franchise_detail.json()["status"] == "ACTIVE"
        assert franchise_detail.json()["profile"]["gstin"] == "22ABCDE1234F1Z5"

        updated = client.patch(
            f"/api/crm/franchises/{franchise['id']}",
            params={"tenant_id": tenant_a},
            json={"name": "North Franchise Updated", "profile": {"enable_email": True}},
            headers=HEADERS,
        )
        assert updated.status_code == 200
        assert updated.json()["name"] == "North Franchise Updated"
        assert updated.json()["profile"] == {"enable_email": True}

        branches = client.get(
            "/api/crm/branches",
            params={"tenant_id": tenant_a, "franchise_id": franchise["id"]},
            headers=HEADERS,
        )
        assert branches.status_code == 200
        assert branches.json()[0]["branch_code"] == "B-001"
        assert branches.json()[0]["franchise_name"] == "North Franchise Updated"

        branch_detail = client.get(f"/api/crm/branches/{branch['id']}", params={"tenant_id": tenant_a}, headers=HEADERS)
        assert branch_detail.status_code == 200
        assert branch_detail.json()["name"] == "Central Branch"

        assert client.get(f"/api/crm/franchises/{franchise['id']}", params={"tenant_id": tenant_b}, headers=HEADERS).status_code == 404
        assert client.get(f"/api/crm/branches/{branch['id']}", params={"tenant_id": tenant_b}, headers=HEADERS).status_code == 404
        assert client.get("/api/crm/branches", params={"tenant_id": tenant_b, "franchise_id": franchise["id"]}, headers=HEADERS).status_code == 404
