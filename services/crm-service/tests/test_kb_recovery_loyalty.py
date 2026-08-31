"""CRM KB feedback, experience recovery, loyalty scoring tests (Batch 8)."""
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app

HEADERS = {"X-CRM-Service-Key": "test-internal-key"}


def _tenant(client):
    return client.post("/api/crm/tenants", json={"name": f"tenant-{uuid4().hex}"},
                       headers=HEADERS).json()["id"]


def test_kb_feedback_loop():
    with TestClient(app) as c:
        tid = _tenant(c)
        fb = c.post("/api/crm/kb/feedback", headers=HEADERS, params={"tenant_id": tid}, json={
            "article_id": "ART-100", "rating": 2, "helpful": False,
            "feedback": "Step 3 missing reboot command"})
        assert fb.status_code == 201
        fid = fb.json()["id"]
        assert fb.json()["applied"] is False
        appd = c.post(f"/api/crm/kb/feedback/{fid}/apply", headers=HEADERS,
                      params={"tenant_id": tid})
        assert appd.json()["applied"] is True
        rl = c.get("/api/crm/kb/feedback", headers=HEADERS, params={"tenant_id": tid}).json()
        assert len(rl) == 1


def test_experience_recovery_engine():
    with TestClient(app) as c:
        tid = _tenant(c)
        r = c.post("/api/crm/recovery/trigger", headers=HEADERS, params={"tenant_id": tid}, json={
            "customer_id": "CUST-500", "metric": "qoe", "degraded_value": 2.1,
            "threshold": 3.0, "recovery_action": "AUTO_REBOOT_CPE"})
        assert r.status_code == 201
        assert r.json()["status"] == "TRIGGERED"
        rl = c.get("/api/crm/recovery", headers=HEADERS, params={"tenant_id": tid}).json()
        assert rl[0]["customer_id"] == "CUST-500"


def test_behavioral_loyalty_scoring():
    with TestClient(app) as c:
        tid = _tenant(c)
        s = c.post("/api/crm/loyalty/score", headers=HEADERS, params={"tenant_id": tid}, json={
            "customer_id": "CUST-500", "period": "MONTH", "score": 87.5,
            "behavioral_factors": {"engagement": 0.9, "advocacy": 0.6, "tenure_months": 24}})
        assert s.status_code == 201
        assert s.json()["score"] == 87.5
        # upsert updates the same row
        s2 = c.post("/api/crm/loyalty/score", headers=HEADERS, params={"tenant_id": tid}, json={
            "customer_id": "CUST-500", "period": "MONTH", "score": 90.0})
        assert s2.json()["id"] == s.json()["id"]
        assert s2.json()["score"] == 90.0
        rl = c.get("/api/crm/loyalty", headers=HEADERS, params={"tenant_id": tid}).json()
        assert len(rl) == 1


def test_requires_auth():
    with TestClient(app) as c:
        assert c.get("/api/crm/kb/feedback", params={"tenant_id": str(uuid4())}).status_code == 401
