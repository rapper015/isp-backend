"""Tenancy core-platform AI tests (Batch 8g: 532, 548, 615, 747, 762, 832,
909, 910, 918, 925, 935)."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from conftest import make_token


@pytest.fixture
def client(defaults):
    with TestClient(app) as c:
        yield c


def test_sentiment_analysis(client, auth_headers):
    r = client.post("/api/tenancy/governance/sentiment", headers=auth_headers, json={
        "text": "The service is great and fast!"})
    assert r.status_code == 201
    assert r.json()["sentiment"] == "POSITIVE"
    rl = client.get("/api/tenancy/governance/sentiment", headers=auth_headers)
    assert len(rl.json()) == 1


def test_smart_reply_suggestion(client, auth_headers):
    r = client.post("/api/tenancy/governance/smart-reply", headers=auth_headers, json={
        "context": "My internet is slow today"})
    assert r.status_code == 201
    assert "line test" in r.json()["suggested_reply"].lower()


def test_consensus_leader_election(client, auth_headers):
    r = client.post("/api/tenancy/governance/consensus/elect", headers=auth_headers, json={
        "cluster": "core", "node_id": "node-3", "term": 4, "votes": 3, "total_nodes": 5})
    assert r.status_code == 201
    assert r.json()["is_leader"] is True


def test_beta_rollout(client, auth_headers):
    r = client.post("/api/tenancy/governance/beta-rollouts", headers=auth_headers, json={
        "feature": "new-billing-ui", "version": "v2.1", "cohort_pct": 10.0})
    assert r.status_code == 201
    assert r.json()["status"] == "BETA"


def test_carbon_footprint(client, auth_headers):
    r = client.post("/api/tenancy/governance/carbon", headers=auth_headers, json={
        "scope": "SCOPE2", "co2_kg": 1250.5, "period": "MONTH"})
    assert r.status_code == 201
    assert r.json()["co2_kg"] == 1250.5


def test_intent_orchestration(client, auth_headers):
    r = client.post("/api/tenancy/governance/intents", headers=auth_headers, json={
        "intent": "INCREASE_BANDWIDTH", "action": "provision 100Mbps upgrade"})
    assert r.status_code == 201
    assert r.json()["status"] == "EXECUTED"


def test_clause_extraction(client, auth_headers):
    r = client.post("/api/tenancy/governance/clauses/extract", headers=auth_headers, json={
        "document_id": "DOC-77", "clause_type": "TERMINATION",
        "clause_text": "Either party may terminate with 30 days notice."})
    assert r.status_code == 201
    assert r.json()["clause_type"] == "TERMINATION"


def test_risk_detection_and_supplier_risk(client, auth_headers):
    r = client.post("/api/tenancy/governance/risk", headers=auth_headers, json={
        "entity": "contract", "entity_id": "CON-1", "score": 90})
    assert r.status_code == 201
    assert r.json()["risk_level"] == "CRITICAL"
    s = client.post("/api/tenancy/governance/risk", headers=auth_headers, json={
        "entity": "supplier", "entity_id": "SUP-9", "score": 45})
    assert s.json()["risk_level"] == "MEDIUM"
    rl = client.get("/api/tenancy/governance/risk", headers=auth_headers)
    assert len(rl.json()) == 2


def test_strategic_planning_ai(client, auth_headers):
    r = client.post("/api/tenancy/governance/strategy", headers=auth_headers, json={
        "objective": "Grow FTTH coverage 20%", "recommendation": "Expand into tier-2 clusters"})
    assert r.status_code == 201
    assert r.json()["objective"].startswith("Grow")


def test_ethics_engine(client, auth_headers):
    ok = client.post("/api/tenancy/governance/ethics", headers=auth_headers, json={
        "decision": "Offer the same plan to all subscribers"})
    assert ok.json()["ethical"] is True
    bad = client.post("/api/tenancy/governance/ethics", headers=auth_headers, json={
        "decision": "Exclude low-income areas from fiber rollout"})
    assert bad.json()["ethical"] is False


def test_tenant_isolation(client, auth_headers):
    other = {"Authorization": f"Bearer {make_token('TENANT_ADMIN', uuid.uuid4())}"}
    client.post("/api/tenancy/governance/sentiment", headers=auth_headers, json={"text": "great"})
    rl = client.get("/api/tenancy/governance/sentiment", headers=other)
    assert rl.json() == []
