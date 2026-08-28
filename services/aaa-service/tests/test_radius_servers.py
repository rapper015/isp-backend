from uuid import uuid4
from fastapi.testclient import TestClient
from app.main import app

def test_radius_server_registration_never_returns_its_api_key():
    with TestClient(app) as client:
        headers = {"X-AAA-Service-Key": "test-internal-key"}
        response = client.post("/api/aaa/radius-servers", headers=headers, json={"name": f"radius-{uuid4().hex}", "host": "192.0.2.9", "internal_api_key": "this-is-not-a-real-test-api-key"})
        assert response.status_code == 200
        assert "api_key" not in response.text.replace("api_key_stored", "")
