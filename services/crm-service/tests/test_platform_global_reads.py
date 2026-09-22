"""Platform operators can browse and open tenant-owned CRM records globally."""
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app


HEADERS = {"X-CRM-Service-Key": "test-internal-key"}


def test_platform_directory_and_detail_reads_do_not_require_tenant_query():
    with TestClient(app) as client:
        tenant = client.post(
            "/api/crm/tenants",
            json={"name": f"global-read-{uuid4().hex}"},
            headers=HEADERS,
        ).json()
        tenant_id = tenant["id"]
        lead = client.post(
            f"/api/crm/leads?tenant_id={tenant_id}",
            json={
                "first_name": "Global",
                "last_name": "Reader",
                "primary_mobile": f"9{uuid4().int % 100000000:08d}",
                "lead_source": "API",
            },
            headers=HEADERS,
        ).json()
        customer = client.post(
            f"/api/crm/customers?tenant_id={tenant_id}",
            json={
                "full_name": "Global Customer",
                "phone": f"8{uuid4().int % 100000000:08d}",
            },
            headers=HEADERS,
        ).json()

        assert client.get("/api/crm/leads", headers=HEADERS).status_code == 200
        assert client.get(f"/api/crm/leads/{lead['id']}", headers=HEADERS).status_code == 200
        assert client.get("/api/crm/customers", headers=HEADERS).status_code == 200
        assert client.get(f"/api/crm/customers/{customer['id']}", headers=HEADERS).status_code == 200
        assert client.get(f"/api/crm/customers/{customer['id']}/360", headers=HEADERS).status_code == 200
        assert client.get(f"/api/crm/customers/{customer['id']}/timeline", headers=HEADERS).status_code == 200
        assert client.get("/api/crm/audit", headers=HEADERS).status_code == 200
