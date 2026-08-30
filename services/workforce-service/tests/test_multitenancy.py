"""Tenant isolation, RBAC, auth, GPS ingest."""
from conftest import make_technician, make_work_order

import os
import uuid
import jwt


def _token(role, tenant):
    return jwt.encode({"userId": "test", "role": role, "permissions": [],
                       "tenant_id": str(tenant)},
                      os.environ["WORKFORCE_JWT_SECRET"], algorithm="HS256")


def test_tenant_isolation(client, manager_headers, tenant_id):
    t2 = uuid.uuid4()
    other = {"Authorization": f"Bearer {_token('TENANT_ADMIN', t2)}"}
    make_work_order(client, manager_headers)
    make_work_order(client, other)
    r = client.get("/api/workforce/v1/work-orders", headers=manager_headers)
    assert r.json().__len__() == 1


def test_platform_can_read_all(client, manager_headers, platform_headers):
    make_work_order(client, manager_headers)
    r = client.get("/api/workforce/v1/work-orders", headers=platform_headers)
    assert len(r.json()) == 1


def test_requires_auth(client):
    r = client.get("/api/workforce/v1/work-orders")
    assert r.status_code == 401


def test_rbac_denies_auditor_write(client, auditor_headers):
    r = client.post("/api/workforce/v1/work-orders", headers=auditor_headers,
                    json={"title": "x", "type": "REPAIR"})
    assert r.status_code == 403


def test_internal_gps_ingest(client, internal_headers, tenant_id):
    tid = make_technician(client, {"Authorization": f"Bearer {_token('TENANT_ADMIN', tenant_id)}"}
                          ).json()["id"]
    r = client.post("/api/workforce/v1/internal/ingest/location",
                    headers=internal_headers,
                    json={"technician_id": tid, "lat": 19.07, "lon": 72.87})
    assert r.status_code == 200
    assert r.json()["lat"] == 19.07


def test_internal_ingest_bad_key(client, tenant_id):
    r = client.post("/api/workforce/v1/internal/ingest/location",
                    headers={"X-Internal-API-Key": "wrong"},
                    json={"technician_id": str(uuid.uuid4()), "lat": 1.0, "lon": 1.0})
    assert r.status_code == 401


def test_health_and_status(client):
    assert client.get("/health").json()["status"] == "ok"
    st = client.get("/status").json()
    assert "workforce.workorder.completed.v1" in st["published_events"]
