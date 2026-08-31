"""OSS OTT partner + pole management tests (Batch 8: 659, 1134)."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_ott_partner_integration(client, auth_headers, tenant_id):
    r = client.post("/api/oss/ott/partners", headers=auth_headers, json={
        "tenant_id": str(tenant_id), "partner_name": "Netflix",
        "provider_type": "VIDEO", "api_endpoint": "https://api.netflix.com/v2"})
    assert r.status_code == 201
    assert r.json()["partner_name"] == "Netflix"
    assert r.json()["status"] == "ACTIVE"
    rl = client.get(f"/api/oss/ott/partners?tenant_id={tenant_id}", headers=auth_headers)
    assert len(rl.json()) == 1


def test_ott_tenant_isolation(client, auth_headers, tenant_id):
    other = uuid.uuid4()
    client.post("/api/oss/ott/partners", headers=auth_headers, json={
        "tenant_id": str(tenant_id), "partner_name": "Spotify",
        "provider_type": "MUSIC"})
    # management_auth fail-closes cross-tenant reads with 403
    assert client.get(f"/api/oss/ott/partners?tenant_id={other}",
                      headers=auth_headers).status_code == 403
    rl = client.get(f"/api/oss/ott/partners?tenant_id={tenant_id}", headers=auth_headers)
    assert len(rl.json()) == 1


def test_pole_tracking(client, auth_headers, tenant_id):
    r = client.post("/api/oss/poles", headers=auth_headers, json={
        "tenant_id": str(tenant_id), "pole_code": "POLE-001",
        "location": "Sector 12, Gurgaon", "pole_type": "CONCRETE", "height_m": 9.0})
    assert r.status_code == 201
    assert r.json()["pole_code"] == "POLE-001"
    rl = client.get(f"/api/oss/poles?tenant_id={tenant_id}", headers=auth_headers)
    assert len(rl.json()) == 1
    assert rl.json()[0]["height_m"] == 9.0


def test_pole_tenant_isolation(client, auth_headers, tenant_id):
    other = uuid.uuid4()
    client.post("/api/oss/poles", headers=auth_headers, json={
        "tenant_id": str(tenant_id), "pole_code": "POLE-002"})
    assert client.get(f"/api/oss/poles?tenant_id={other}",
                      headers=auth_headers).status_code == 403
    rl = client.get(f"/api/oss/poles?tenant_id={tenant_id}", headers=auth_headers)
    assert len(rl.json()) == 1


def test_requires_auth(client, tenant_id):
    assert client.get(f"/api/oss/ott/partners?tenant_id={tenant_id}").status_code == 401
