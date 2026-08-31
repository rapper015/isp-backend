"""SIEM compliance-ops tests (Batch 7: 403, 1164, 1236, 1370)."""
from conftest import make_token


def _mgr(client, tenant_id):
    return {"Authorization": f"Bearer {make_token('COMPLIANCE_OFFICER', tenant_id)}"}


def test_circle_region_mapping(client, tenant_id):
    h = _mgr(client, tenant_id)
    r = client.post("/api/siem/v1/compliance/circles", headers=h, json={
        "operator": "ISP-X", "circle_name": "MH&GOA", "state_codes": ["MH", "GA"]})
    assert r.status_code == 201
    assert r.json()["circle_name"] == "MH&GOA"
    rl = client.get("/api/siem/v1/compliance/circles", headers=h)
    assert len(rl.json()) == 1


def test_geo_blocking_evaluate(client, tenant_id):
    h = _mgr(client, tenant_id)
    client.post("/api/siem/v1/compliance/geo-block", headers=h, json={
        "service": "STREAMING", "region_code": "IN-WB", "action": "BLOCK"})
    blocked = client.post("/api/siem/v1/compliance/geo-block/evaluate", headers=h, json={
        "service": "STREAMING", "region_code": "IN-WB"})
    assert blocked.json()["action"] == "BLOCK"
    allowed = client.post("/api/siem/v1/compliance/geo-block/evaluate", headers=h, json={
        "service": "STREAMING", "region_code": "IN-MH"})
    assert allowed.json()["action"] == "ALLOW"


def test_threat_playbook_execution(client, tenant_id):
    h = _mgr(client, tenant_id)
    p = client.post("/api/siem/v1/threat/playbooks", headers=h, json={
        "name": "Lateral movement hunt", "tactic": "TA0008",
        "steps": ["Query auth logs", "Correlate source IPs", "Quarantine"]})
    assert p.status_code == 201
    pid = p.json()["id"]
    ex = client.post(f"/api/siem/v1/threat/playbooks/{pid}/execute", headers=h)
    assert ex.json()["executions"] == 1
    rl = client.get("/api/siem/v1/threat/playbooks", headers=h)
    assert rl.json()[0]["executions"] == 1


def test_adaptive_mfa_evaluate(client, tenant_id):
    h = _mgr(client, tenant_id)
    client.post("/api/siem/v1/security/mfa-rules", headers=h, json={
        "name": "High risk login", "conditions": {"risk_score": 70, "geo_mismatch": True},
        "trigger_action": "CHALLENGE"})
    r = client.post("/api/siem/v1/security/mfa-rules/evaluate", headers=h, json={
        "context": {"risk_score": 85, "geo_mismatch": True}})
    assert r.json()["mfa_required"] is True
    r2 = client.post("/api/siem/v1/security/mfa-rules/evaluate", headers=h, json={
        "context": {"risk_score": 20, "geo_mismatch": False}})
    assert r2.json()["mfa_required"] is False


def test_geo_requires_tenant(client, platform_headers):
    r = client.post("/api/siem/v1/compliance/circles", headers=platform_headers,
                    json={"operator": "X", "circle_name": "C", "state_codes": []})
    assert r.status_code == 400  # platform scope without tenant
