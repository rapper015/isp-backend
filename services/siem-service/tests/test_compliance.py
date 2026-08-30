"""Compliance: policies, violations, retention, consent, DSAR (features 401, 404-406, 421-423, 426, 441-442)."""
from conftest import ingest_event

import uuid


def test_policy_crud(client, soc_headers):
    r = client.post("/api/siem/v1/policies", headers=soc_headers, json={
        "name": "Brute-force lockdown", "category": "AUTH",
        "rule_json": {"field": "severity", "op": "eq", "value": "HIGH"},
        "severity": "HIGH"})
    assert r.status_code == 201
    pid = r.json()["id"]
    rl = client.get("/api/siem/v1/policies", headers=soc_headers)
    assert any(p["id"] == pid for p in rl.json())


def test_violation_detected_on_ingest(client, soc_headers):
    client.post("/api/siem/v1/policies", headers=soc_headers, json={
        "name": "High severity", "category": "AUTH",
        "rule_json": {"field": "severity", "op": "eq", "value": "HIGH"},
        "severity": "CRITICAL"})
    ingest_event(client, soc_headers)  # HIGH -> matches
    r = client.get("/api/siem/v1/violations", headers=soc_headers)
    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.json()[0]["severity"] == "CRITICAL"


def test_violation_resolve(client, soc_headers):
    client.post("/api/siem/v1/policies", headers=soc_headers, json={
        "name": "P", "category": "AUTH",
        "rule_json": {"field": "severity", "op": "eq", "value": "HIGH"}})
    ingest_event(client, soc_headers)
    vid = client.get("/api/siem/v1/violations", headers=soc_headers).json()[0]["id"]
    r = client.post(f"/api/siem/v1/violations/{vid}/resolve", headers=soc_headers)
    assert r.json()["status"] == "RESOLVED"


def test_evaluate_policy_endpoint(client, soc_headers):
    pid = client.post("/api/siem/v1/policies", headers=soc_headers, json={
        "name": "P", "category": "AUTH",
        "rule_json": {"field": "severity", "op": "eq", "value": "HIGH"}}).json()["id"]
    ingest_event(client, soc_headers)
    r = client.post(f"/api/siem/v1/policies/{pid}/evaluate", headers=soc_headers)
    assert r.status_code == 200
    assert r.json()["matched"] >= 1


def test_retention_purge(client, soc_headers, tenant_id):
    client.post("/api/siem/v1/retention-policies", headers=soc_headers, json={
        "data_class": "SECURITY_EVENT", "retention_days": 1, "action": "PURGE"})
    ingest_event(client, soc_headers)
    from datetime import datetime, timedelta, timezone
    from app import models
    from app.database import SessionLocal
    db = SessionLocal()
    row = db.query(models.SecurityEvent).first()
    row.received_at = datetime.now(timezone.utc) - timedelta(days=5)
    db.commit()
    r = client.post("/api/siem/v1/retention/run", headers=soc_headers, json={})
    assert r.json()["purged"] == 1
    assert db.query(models.SecurityEvent).count() == 0
    db.close()


def test_retention_archive_flags(client, soc_headers):
    client.post("/api/siem/v1/retention-policies", headers=soc_headers, json={
        "data_class": "SECURITY_EVENT", "retention_days": 1, "action": "ARCHIVE"})
    ingest_event(client, soc_headers)
    from datetime import datetime, timedelta, timezone
    from app import models
    from app.database import SessionLocal
    db = SessionLocal()
    row = db.query(models.SecurityEvent).first()
    row.received_at = datetime.now(timezone.utc) - timedelta(days=5)
    db.commit()
    r = client.post("/api/siem/v1/retention/run", headers=soc_headers, json={})
    assert r.json()["archived"] == 1
    db.expire_all()
    assert db.query(models.SecurityEvent).first().archived is True
    db.close()


def test_consent_grant_and_revoke(client, soc_headers):
    r = client.post("/api/siem/v1/consent", headers=soc_headers, json={
        "subscriber_id": "sub-1001", "purpose": "MARKETING", "status": "GRANTED"})
    assert r.status_code == 201
    cid = r.json()["id"]
    r2 = client.post("/api/siem/v1/consent", headers=soc_headers, json={
        "subscriber_id": "sub-1001", "purpose": "MARKETING", "status": "REVOKED"})
    assert r2.json()["id"] == cid
    assert r2.json()["status"] == "REVOKED"


def test_dsar_access_fulfill(client, soc_headers):
    r = client.post("/api/siem/v1/data-requests", headers=soc_headers, json={
        "requester_id": "user-9", "subject_id": "sub-1001", "request_type": "ACCESS"})
    assert r.status_code == 201
    rid = r.json()["id"]
    r2 = client.post(f"/api/siem/v1/data-requests/{rid}/fulfill", headers=soc_headers)
    assert r2.json()["status"] == "FULFILLED"


def test_right_to_erasure(client, soc_headers):
    client.post("/api/siem/v1/consent", headers=soc_headers, json={
        "subscriber_id": "sub-1001", "purpose": "MARKETING"})
    ingest_event(client, soc_headers, actor="sub-1001")
    r = client.post("/api/siem/v1/data-requests", headers=soc_headers, json={
        "requester_id": "user-9", "subject_id": "sub-1001", "request_type": "ERASURE"})
    rid = r.json()["id"]
    r2 = client.post(f"/api/siem/v1/data-requests/{rid}/erase", headers=soc_headers)
    assert r2.json()["status"] == "FULFILLED"
    from app import models
    from app.database import SessionLocal
    db = SessionLocal()
    assert db.query(models.ConsentRecord).count() == 0
    assert db.query(models.SecurityEvent).filter(
        models.SecurityEvent.actor == "sub-1001").count() == 0
    db.close()
