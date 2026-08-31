"""SIEM legal notices + digital forensics tests (Batch 8: 1280, 1443)."""
import uuid

from conftest import make_token


def _h(client, tenant_id, role="SECURITY_OPS"):
    return {"Authorization": f"Bearer {make_token(role, tenant_id)}"}


def test_legal_notice_workflow(client, tenant_id):
    h = _h(client, tenant_id)
    n = client.post("/api/siem/v1/notices", headers=h, json={
        "notice_type": "breach", "subject": "Data breach notification",
        "recipient": "regulator@example.com", "details": {"affected": 1200}})
    assert n.status_code == 201
    nid = n.json()["id"]
    assert n.json()["status"] == "DRAFT"
    p = client.post(f"/api/siem/v1/notices/{nid}/process", headers=h)
    assert p.status_code == 200
    assert p.json()["status"] == "SERVED"
    assert p.json()["served_at"] is not None
    rl = client.get("/api/siem/v1/notices", headers=h)
    assert len(rl.json()) == 1


def test_forensics_investigation(client, tenant_id):
    h = _h(client, tenant_id)
    inv = client.post("/api/siem/v1/forensics", headers=h, json={
        "case_ref": "CASE-1001", "scope": "endpoint-42",
        "evidence_items": [{"hash": "abc123", "source": "disk"}],
        "timeline": [{"ts": "2026-08-31T10:00:00Z", "action": "exec"}]})
    assert inv.status_code == 201
    iid = inv.json()["id"]
    assert inv.json()["status"] == "OPEN"
    done = client.post(f"/api/siem/v1/forensics/{iid}/complete", headers=h,
                       json={"findings": "Malware confirmed; lateral movement via SMB"})
    assert done.status_code == 200
    assert done.json()["status"] == "COMPLETE"
    assert done.json()["evidence_count"] == 1
    rl = client.get("/api/siem/v1/forensics", headers=h)
    assert len(rl.json()) == 1


def test_notice_tenant_isolation(client, tenant_id):
    a = _h(client, tenant_id)
    b = _h(client, uuid.uuid4())
    client.post("/api/siem/v1/notices", headers=a, json={
        "notice_type": "dmca", "subject": "DMCA", "recipient": "x@x.com"})
    rl = client.get("/api/siem/v1/notices", headers=b)
    assert rl.json() == []


def test_forensics_not_found(client, tenant_id):
    h = _h(client, tenant_id)
    r = client.post(f"/api/siem/v1/forensics/{uuid.uuid4()}/complete", headers=h,
                    json={"findings": "none"})
    assert r.status_code == 404


def test_notices_require_permission(client, tenant_id):
    ro = _h(client, tenant_id, role="READ_ONLY")
    r = client.post("/api/siem/v1/notices", headers=ro, json={
        "notice_type": "x", "subject": "x", "recipient": "x@x.com"})
    assert r.status_code == 403
