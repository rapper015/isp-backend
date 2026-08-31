"""Event ingestion, tamper-evidence, search, export (features 407-410, 448)."""
from conftest import ingest_event

import uuid


def test_ingest_single_event(client, soc_headers):
    r = ingest_event(client, soc_headers)
    assert r.status_code == 201
    body = r.json()
    assert body["event_type"] == "AUTH.LOGIN_FAILED"
    assert body["digest"]
    assert body["block_index"] == 1


def test_ingest_rejects_no_tenant(client, platform_headers):
    r = ingest_event(client, platform_headers)
    assert r.status_code == 400


def test_pii_is_masked(client, soc_headers):
    r = ingest_event(client, soc_headers, payload={"username": "alice@example.com", "phone": "+919876543210"})
    body = r.json()
    assert body["masked_payload"]["username"] == "al***@example.com"
    assert body["masked_payload"]["phone"].endswith("3210")
    assert "*" in body["masked_payload"]["phone"]
    assert body["payload"]["username"] == "alice@example.com"  # raw retained for SIEM


def test_sensitive_fields_redacted(client, soc_headers):
    r = ingest_event(client, soc_headers, payload={"password": "s3cret", "token": "abc"})
    body = r.json()
    assert body["masked_payload"]["password"] == "***REDACTED***"


def test_evidence_hash_chain_verifies(client, soc_headers, tenant_id):
    a = ingest_event(client, soc_headers, event_type="A.B", severity="LOW").json()
    b = ingest_event(client, soc_headers, event_type="C.D", severity="HIGH").json()
    assert a["block_index"] == 1
    assert b["block_index"] == 2
    assert b["prev_hash"] == a["digest"]
    ev = client.get(f"/api/siem/v1/security-events/{b['id']}/evidence", headers=soc_headers)
    assert ev.status_code == 200
    assert ev.json()["verified"] is True
    assert len(ev.json()["blocks"]) == 1


def test_evidence_detects_tamper(client, soc_headers, session, tenant_id):
    r = ingest_event(client, soc_headers).json()
    from app import models
    row = session.query(models.SecurityEvent).filter(
        models.SecurityEvent.id == uuid.UUID(r["id"])).first()
    row.payload = {"username": "tampered@example.com"}
    session.commit()
    ev = client.get(f"/api/siem/v1/security-events/{r['id']}/evidence", headers=soc_headers)
    assert ev.status_code == 200
    assert ev.json()["verified"] is False


def test_search_and_filter(client, soc_headers):
    ingest_event(client, soc_headers, event_type="AUTH.LOGIN_FAILED", severity="HIGH")
    ingest_event(client, soc_headers, event_type="NET.FLOW", severity="INFO")
    r = client.get("/api/siem/v1/security-events?event_type=AUTH.LOGIN_FAILED", headers=soc_headers)
    assert r.json()["total"] == 1
    r2 = client.get("/api/siem/v1/security-events?q=NET", headers=soc_headers)
    assert r2.json()["total"] == 1
    r3 = client.get("/api/siem/v1/security-events?source_ip=203.0.113.7", headers=soc_headers)
    assert r3.json()["total"] == 2


def test_export_ndjson(client, soc_headers):
    ingest_event(client, soc_headers)
    ingest_event(client, soc_headers, event_type="NET.FLOW")
    r = client.post("/api/siem/v1/security-events/export",
                    json={"format": "ndjson"}, headers=soc_headers)
    assert r.status_code == 200
    assert "application/x-ndjson" in r.headers["content-type"]
    assert r.text.count("\n") >= 1


def test_bulk_internal_ingest(client, internal_headers, tenant_id):
    events = [{"event_type": f"AGENT.{i}", "category": "RUNTIME", "severity": "LOW",
               "source_ip": f"10.0.0.{i}", "payload": {"n": i}} for i in range(50)]
    r = client.post("/api/siem/v1/internal/ingest/events",
                    json={"events": events, "tenant_id": str(tenant_id)},
                    headers=internal_headers)
    assert r.status_code == 200
    assert r.json()["ingested"] == 50


def test_internal_ingest_rejects_bad_key(client):
    r = client.post("/api/siem/v1/internal/ingest/events", json={"events": []},
                    headers={"X-Internal-API-Key": "wrong"})
    assert r.status_code == 401
