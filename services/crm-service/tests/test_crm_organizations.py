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
        assert updated.json()["profile"]["enable_email"] is True
        assert updated.json()["profile"]["enable_sms"] is False

        settings = client.patch(
            f"/api/crm/franchises/{franchise['id']}/settings",
            params={"tenant_id": tenant_a},
            json={"settings": {"enable_sms": True, "static_isp_share": 30, "static_reseller_share": 70}, "reason": "Enable franchise messaging and revenue split"},
            headers=HEADERS,
        )
        assert settings.status_code == 200
        assert settings.json()["settings"]["enable_sms"] is True
        assert settings.json()["settings"]["static_reseller_share"] == "70"

        capability = client.get(
            f"/internal/crm/franchises/{franchise['id']}/capabilities/sms",
            params={"tenant_id": tenant_a}, headers=HEADERS,
        )
        assert capability.status_code == 200
        assert capability.json()["enabled"] is True

        history = client.get(
            f"/api/crm/franchises/{franchise['id']}/settings/history",
            params={"tenant_id": tenant_a}, headers=HEADERS,
        )
        assert history.status_code == 200
        assert history.json()[0]["reason"] == "Enable franchise messaging and revenue split"

        invalid_share = client.patch(
            f"/api/crm/franchises/{franchise['id']}/settings",
            params={"tenant_id": tenant_a},
            json={"settings": {"static_reseller_share": 101}, "reason": "Invalid test"}, headers=HEADERS,
        )
        assert invalid_share.status_code == 422

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
