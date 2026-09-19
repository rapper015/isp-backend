from fastapi.testclient import TestClient

from app.main import app


def test_plan_detail_and_update():
    with TestClient(app) as client:
        created = client.post("/plans", json={
            "plan_code": "TEST-FIBER-100",
            "name": "Test Fiber 100",
            "monthly_fee": "999.00",
            "download_rate_kbps": 102400,
            "upload_rate_kbps": 51200,
        })
        assert created.status_code == 201
        plan_id = created.json()["id"]

        detail = client.get(f"/plans/{plan_id}")
        assert detail.status_code == 200
        assert detail.json()["name"] == "Test Fiber 100"

        updated = client.patch(f"/plans/{plan_id}", json={
            "name": "Test Fiber 100 Plus",
            "monthly_fee": "1099.00",
            "download_rate_kbps": 204800,
        })
        assert updated.status_code == 200
        assert updated.json()["name"] == "Test Fiber 100 Plus"
        assert updated.json()["monthly_fee"] == "1099.00"
        assert updated.json()["upload_rate_kbps"] == 51200


def test_plan_detail_returns_404_for_unknown_id():
    with TestClient(app) as client:
        response = client.get("/plans/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404
