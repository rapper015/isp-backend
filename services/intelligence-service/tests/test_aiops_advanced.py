"""Intelligence aiops-advanced tests (Batch 8h: 731, 739, 861, 871, 883, 886,
888, 898)."""
from conftest import make_token


def _tok(role, tenant):
    return {"Authorization": f"Bearer {make_token(role, tenant)}"}


def test_network_digital_twin(client, tenant_id):
    h = _tok("AI_ENGINEER", tenant_id)
    r = client.post("/api/intelligence/v1/aiops/network-twin", json={
        "twin_name": "prod-core", "topology": {"nodes": 12, "links": 30},
        "state": {"latency_ms": 3.2}}, headers=h)
    assert r.status_code == 200
    assert r.json()["twin_name"] == "prod-core"
    rl = client.get("/api/intelligence/v1/aiops/network-twin", headers=h)
    assert len(rl.json()) == 1


def test_autonomous_scaling(client, tenant_id):
    h = _tok("NOC_ENGINEER", tenant_id)
    r = client.post("/api/intelligence/v1/aiops/scaling", json={
        "service": "billing-api", "action": "SCALE_UP",
        "reason": "CPU > 80% for 15m"}, headers=h)
    assert r.status_code == 200
    assert r.json()["action"] == "SCALE_UP"


def test_autonomous_pricing(client, tenant_id):
    h = _tok("FINANCE_OPS", tenant_id)
    r = client.post("/api/intelligence/v1/aiops/pricing", json={
        "product": "fiber-500", "old_price": 999.0, "new_price": 899.0,
        "reason": "demand elasticity"}, headers=h)
    assert r.status_code == 200
    assert r.json()["new_price"] == 899.0


def test_business_digital_twin(client, tenant_id):
    h = _tok("AI_ENGINEER", tenant_id)
    r = client.post("/api/intelligence/v1/aiops/business-twin", json={
        "twin_name": "ftth-expansion", "scenario": "2x capex",
        "metrics": {"payback_years": 3.1}}, headers=h)
    assert r.status_code == 200
    assert r.json()["scenario"] == "2x capex"


def test_upsell_engine(client, tenant_id):
    h = _tok("TENANT_ADMIN", tenant_id)
    r = client.post("/api/intelligence/v1/aiops/upsell", json={
        "customer_id": "CUST-9", "product": "fiber-1000",
        "rationale": "usage > 80% of current plan"}, headers=h)
    assert r.status_code == 200
    assert r.json()["product"] == "fiber-1000"


def test_voice_assistant(client, tenant_id):
    h = _tok("TENANT_ADMIN", tenant_id)
    r = client.post("/api/intelligence/v1/aiops/voice", json={
        "query": "I want to pay my bill"}, headers=h)
    assert r.status_code == 200
    assert "payment" in r.json()["response"].lower()


def test_sentiment_response(client, tenant_id):
    h = _tok("AI_ENGINEER", tenant_id)
    r = client.post("/api/intelligence/v1/aiops/sentiment", headers=h, json={
        "sentiment": "NEGATIVE"})
    assert r.status_code == 200
    assert "credit" in r.json()["action"].lower()


def test_digital_workforce(client, tenant_id):
    h = _tok("AI_ENGINEER", tenant_id)
    r = client.post("/api/intelligence/v1/aiops/workforce", headers=h, json={
        "task_name": "ticket-triage", "automation_pct": 92.0})
    assert r.status_code == 200
    assert r.json()["status"] == "AUTOMATED"


def test_requires_auth(client, tenant_id):
    assert client.post("/api/intelligence/v1/aiops/upsell",
                       json={"customer_id": "C", "product": "P"}).status_code == 401
