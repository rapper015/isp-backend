"""M3 API end-to-end: policy lifecycle, assignment + explain, control actions,
readiness, and FUP via the HTTP API (internal-service auth)."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app

AUTH = {"X-AAA-Service-Key": "test-internal-key"}


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _policy_payload(tenant_id):
    return {
        "tenant_id": str(tenant_id),
        "code": f"fiber-{uuid.uuid4().hex[:6]}",
        "name": "Fiber 100",
        "body": {"upload_kbps": 10000, "download_kbps": 50000, "ipv4_pool": "pppoe-pool"},
    }


def test_policy_lifecycle_end_to_end(client, tenant_id):
    created = client.post("/api/aaa/policies", json=_policy_payload(tenant_id), headers=AUTH)
    assert created.status_code == 201
    policy_id = created.json()["id"]

    v = client.post(f"/api/aaa/policies/{policy_id}/versions", json={"tenant_id": str(tenant_id), "body": {"upload_kbps": 20000, "download_kbps": 100000}, "actor": "tester"}, headers=AUTH)
    assert v.status_code == 201
    version = v.json()["version"]

    r = client.post(f"/api/aaa/policies/{policy_id}/versions/{version}/validate", params={"tenant_id": str(tenant_id)}, headers=AUTH)
    assert r.json()["valid"] is True

    r = client.post(f"/api/aaa/policies/{policy_id}/versions/{version}/preview", params={"tenant_id": str(tenant_id)}, headers=AUTH)
    assert r.json()["radius_attributes"]["Mikrotik-Rate-Limit"] == "100M/20M"

    assert client.post(f"/api/aaa/policies/{policy_id}/versions/{version}/submit", params={"tenant_id": str(tenant_id)}, headers=AUTH).json()["state"] == "UNDER_REVIEW"
    assert client.post(f"/api/aaa/policies/{policy_id}/versions/{version}/approve", params={"tenant_id": str(tenant_id)}, headers=AUTH).json()["state"] == "APPROVED"
    scheduled = client.post(
        f"/api/aaa/policies/{policy_id}/versions/{version}/schedule",
        json={"tenant_id": str(tenant_id), "effective_from": "2026-09-01T00:00:00Z", "actor": "tester"},
        headers=AUTH,
    )
    assert scheduled.json()["state"] == "SCHEDULED"
    active = client.post(f"/api/aaa/policies/{policy_id}/versions/{version}/activate", params={"tenant_id": str(tenant_id)}, headers=AUTH)
    assert active.json()["state"] == "ACTIVE"


def test_explain_effective_policy_with_assignment(client, tenant_id, subscriber):
    # create + activate a policy, assign to subscriber, then explain
    created = client.post("/api/aaa/policies", json=_policy_payload(tenant_id), headers=AUTH).json()
    policy_id = created["id"]
    v = client.post(f"/api/aaa/policies/{policy_id}/versions", json={"tenant_id": str(tenant_id), "body": {"upload_kbps": 10000, "download_kbps": 50000}, "actor": "tester"}, headers=AUTH).json()
    version = v["version"]
    client.post(f"/api/aaa/policies/{policy_id}/versions/{version}/submit", params={"tenant_id": str(tenant_id)}, headers=AUTH)
    client.post(f"/api/aaa/policies/{policy_id}/versions/{version}/approve", params={"tenant_id": str(tenant_id)}, headers=AUTH)
    client.post(f"/api/aaa/policies/{policy_id}/versions/{version}/activate", params={"tenant_id": str(tenant_id)}, headers=AUTH)
    vid = client.get(f"/api/aaa/policies/{policy_id}/versions/{version}", params={"tenant_id": str(tenant_id)}, headers=AUTH).json()["id"]
    client.post(
        f"/api/aaa/subscribers/{subscriber.subscriber_id}/policy-assignment",
        json={"tenant_id": str(tenant_id), "policy_version_id": vid, "source": "subscriber", "actor": "tester"},
        headers=AUTH,
    )
    r = client.post(
        f"/api/aaa/subscribers/{subscriber.subscriber_id}/effective-policy/explain",
        json={"tenant_id": str(tenant_id), "facts": {}},
        headers=AUTH,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["reason_code"] == "PLAN_ENTITLEMENT"
    assert body["radius_attributes"]["Mikrotik-Rate-Limit"] == "50M/10M"
    assert body["decision_id"]


def test_control_action_create_and_outcome(client, session, tenant_id, nas, subscriber):
    from datetime import datetime, timezone

    from app.models import ActiveSession

    active = ActiveSession(
        tenant_id=tenant_id,
        nas_id=nas.id,
        subscriber_id=subscriber.subscriber_id,
        username="cust-a",
        session_id="ses-api-1",
        status="ACTIVE",
        started_at=datetime.now(timezone.utc),
        framed_ip="198.51.100.10",
    )
    session.add(active)
    session.commit()
    session.refresh(active)
    r = client.post(
        "/api/aaa/control-actions",
        json={
            "tenant_id": str(tenant_id),
            "action_type": "COA",
            "trigger": "operator",
            "nas_id": str(nas.id),
            "session_id": str(active.id),
            "subscriber_id": str(subscriber.subscriber_id),
            "username": active.username,
            "session_identifier": {"Acct-Session-Id": active.session_id},
            "requested_attributes": {"Mikrotik-Rate-Limit": "8M/4M"},
            "idempotency_key": f"api-coa-{uuid.uuid4().hex}",
            "actor": "tester",
        },
        headers=AUTH,
    )
    assert r.status_code == 201
    action_id = r.json()["id"]
    assert r.json()["strategy"] == "COA"
    outcome = client.post(
        f"/api/aaa/control-actions/{action_id}/outcome",
        json={"tenant_id": str(tenant_id), "outcome": "ACK", "latency_ms": 9},
        headers=AUTH,
    )
    assert outcome.json()["status"] == "ACK"
    detail = client.get(f"/api/aaa/control-actions/{action_id}", params={"tenant_id": str(tenant_id)}, headers=AUTH)
    assert detail.json()["ack_at"] is not None


def test_network_readiness_and_setup_requirements(client, tenant_id, nas, nas_credential):
    r = client.post("/api/aaa/nas/{}/network-readiness".format(nas.id), params={"tenant_id": str(tenant_id)}, headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in ("READY", "READY_WITH_WARNINGS", "MISSING_CONFIGURATION")
    assert "winbox_guide" in body
    req = client.get("/api/aaa/nas/{}/network-setup-requirements".format(nas.id), params={"tenant_id": str(tenant_id)}, headers=AUTH)
    assert req.status_code == 200


def test_fup_policy_and_preview(client, tenant_id):
    r = client.post(
        "/api/aaa/fup-policies",
        json={"tenant_id": str(tenant_id), "code": "fup-1", "name": "Basic", "cycle": "monthly", "thresholds": [{"label": "t1", "limit_bytes": 1000, "upload_kbps": 100, "download_kbps": 500, "combined": True}]},
        headers=AUTH,
    )
    assert r.status_code == 201
    listed = client.get("/api/aaa/fup-policies", params={"tenant_id": str(tenant_id)}, headers=AUTH)
    assert len(listed.json()) == 1
