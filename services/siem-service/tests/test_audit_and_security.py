"""Audit trail, multitenancy isolation, RBAC, LI, vulnerabilities, dashboard."""
import uuid


def test_audit_trail_records_actions(client, soc_headers):
    _mk_event(client, soc_headers)
    _mk_case(client, soc_headers)
    r = client.get("/api/siem/v1/audit-log", headers=soc_headers)
    actions = [a["action"] for a in r.json()]
    assert "case.create" in actions
    assert r.status_code == 200


def test_audit_export(client, soc_headers):
    _mk_event(client, soc_headers)
    r = client.post("/api/siem/v1/audit-log/export", headers=soc_headers, json={})
    assert r.status_code == 200
    assert "audit-log.json" in r.headers["content-disposition"]


def test_audit_search_filter(client, soc_headers):
    _mk_case(client, soc_headers)
    r = client.get("/api/siem/v1/audit-log?action=case.create", headers=soc_headers)
    assert len(r.json()) == 1


def test_tenant_isolation(client, tenant_headers):
    t2 = uuid.uuid4()
    other_headers = {"Authorization": _token("TENANT_ADMIN", t2)}
    _mk_event(client, tenant_headers)
    _mk_event(client, other_headers)
    r = client.get("/api/siem/v1/security-events", headers=tenant_headers)
    assert r.json()["total"] == 1


def test_platform_can_read_all(client, tenant_headers, platform_headers):
    _mk_event(client, tenant_headers)
    r = client.get("/api/siem/v1/security-events", headers=platform_headers)
    assert r.json()["total"] >= 1


def test_rbac_denies_auditor_write(client, auditor_headers):
    r = client.post("/api/siem/v1/cases", headers=auditor_headers, json={
        "title": "x", "category": "INCIDENT", "severity": "LOW"})
    assert r.status_code == 403


def test_requires_auth(client):
    r = client.get("/api/siem/v1/security-events")
    assert r.status_code == 401


def test_bad_token_rejected(client):
    r = client.get("/api/siem/v1/security-events",
                   headers={"Authorization": "Bearer invalid.token.here"})
    assert r.status_code == 401


def test_li_workflow(client, soc_headers):
    r = client.post("/api/siem/v1/li/requests", headers=soc_headers, json={
        "target_subscriber": "sub-1001", "requester": "LEA-UNIT",
        "authority_ref": "REF-123"})
    assert r.status_code == 201
    rid = r.json()["id"]
    r2 = client.post(f"/api/siem/v1/li/requests/{rid}/decide", headers=soc_headers,
                     json={"decision": "APPROVED", "approver_note": "court order on file"})
    assert r2.json()["status"] == "APPROVED"


def test_vulnerability_ingest_and_remediate(client, soc_headers):
    r = client.post("/api/siem/v1/vulnerabilities", headers=soc_headers, json={
        "target": "gateway-01", "scanner": "trivy", "severity": "HIGH",
        "cve": "CVE-2024-0001"})
    assert r.status_code == 201
    vid = r.json()["id"]
    r2 = client.post(f"/api/siem/v1/vulnerabilities/{vid}/remediate", headers=soc_headers)
    assert r2.json()["status"] == "REMEDIATED"


def test_dashboard_summary(client, soc_headers):
    _mk_event(client, soc_headers, severity="CRITICAL")
    _mk_case(client, soc_headers, severity="CRITICAL")
    r = client.get("/api/siem/v1/dashboard/summary", headers=soc_headers)
    body = r.json()
    assert body["total_events"] == 1
    assert body["critical_severity_events"] == 1
    assert body["open_cases"] == 1


def test_regulatory_report(client, soc_headers):
    _mk_event(client, soc_headers)
    r = client.post("/api/siem/v1/regulatory/reports", headers=soc_headers,
                    json={"type": "compliance_summary", "regulator": "TRAI"})
    assert r.status_code == 200
    assert r.json()["regulator"] == "TRAI"


def test_health_and_status(client):
    assert client.get("/health").json()["status"] == "ok"
    st = client.get("/status").json()
    assert "siem.security_event.ingested.v1" in st["published_events"]


def _token(role, tenant):
    import os
    import jwt as _jwt
    return _jwt.encode({"userId": "test", "role": role,
                        "permissions": [], "tenant_id": str(tenant)},
                       os.environ["SIEM_JWT_SECRET"], algorithm="HS256")


def _mk_event(client, headers, **over):
    body = {"event_type": "AUTH.X", "category": "AUTH", "severity": "MEDIUM",
            "source_ip": "203.0.113.9", "actor": "u", "payload": {}}
    body.update(over)
    return client.post("/api/siem/v1/security-events", json=body, headers=headers)


def _mk_case(client, headers, **over):
    body = {"title": "t", "category": "INCIDENT", "severity": "MEDIUM", "assignee": None,
            "linked_event_ids": []}
    body.update(over)
    return client.post("/api/siem/v1/cases", json=body, headers=headers)
