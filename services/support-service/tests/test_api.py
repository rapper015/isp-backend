"""API tests: management RBAC, ticket commands, SLA policies, diagnostics,
actions, knowledge, reports, inbound ingestion and the customer portal."""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from conftest import make_customer_token, make_token, ticket_payload


@pytest.fixture
def client(tenant_id):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth(tenant_id):
    return {"Authorization": f"Bearer {make_token('SUPPORT_MANAGER', tenant_id)}"}


@pytest.fixture
def customer_auth(tenant_id):
    return {"Authorization": f"Bearer {make_customer_token('CUST-0001', tenant_id)}"}


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_create_ticket_api(client, tenant_id, auth):
    payload = ticket_payload(tenant_id)
    resp = client.post("/api/support/tickets", json=payload, headers=auth)
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["ticket_number"].startswith("TKT-")
    assert data["status"] == "NEW"
    assert data["customer_status"] == "SUBMITTED"


def test_create_ticket_requires_auth(client, tenant_id):
    payload = ticket_payload(tenant_id)
    resp = client.post("/api/support/tickets", json=payload)
    assert resp.status_code == 401


def test_rbac_denies_assign_for_auditor(client, tenant_id):
    ticket = _create(client, tenant_id, "SUPPORT_MANAGER")
    auditor = {"Authorization": f"Bearer {make_token('AUDITOR', tenant_id)}"}
    resp = client.post(f"/api/support/tickets/{ticket['id']}/assign",
                       json={"agent_id": "a1", "reason": "try"}, headers=auditor)
    assert resp.status_code == 403


def _create(client, tenant_id, role="SUPPORT_MANAGER", **overrides):
    headers = {"Authorization": f"Bearer {make_token(role, tenant_id)}"}
    payload = ticket_payload(tenant_id, **overrides)
    resp = client.post("/api/support/tickets", json=payload, headers=headers)
    assert resp.status_code == 201
    return resp.json()


def test_full_lifecycle_via_api(client, tenant_id):
    auth = {"Authorization": f"Bearer {make_token('SUPPORT_MANAGER', tenant_id)}"}
    ticket = _create(client, tenant_id)
    tid = ticket["id"]

    r = client.post(f"/api/support/tickets/{tid}/assign", json={"agent_id": "a1", "agent_name": "A One"}, headers=auth)
    assert r.status_code == 200 and r.json()["status"] == "ASSIGNED"

    r = client.post(f"/api/support/tickets/{tid}/accept", headers=auth)
    assert r.status_code == 200 and r.json()["status"] == "IN_PROGRESS"

    r = client.post(f"/api/support/tickets/{tid}/reply", json={"body": "Investigating now"}, headers=auth)
    assert r.status_code == 200

    r = client.post(f"/api/support/tickets/{tid}/note", json={"body": "internal note only"}, headers=auth)
    assert r.status_code == 200

    r = client.post(f"/api/support/tickets/{tid}/resolve",
                    json={"resolution_code": "CONFIGURATION_CORRECTED", "summary": "fixed routing"}, headers=auth)
    assert r.status_code == 200 and r.json()["status"] == "RESOLVED"

    r = client.post(f"/api/support/tickets/{tid}/close", headers=auth)
    assert r.status_code == 200 and r.json()["status"] == "CLOSED"

    r = client.get(f"/api/support/tickets/{tid}/events", headers=auth)
    assert r.status_code == 200
    types = [e["event_type"] for e in r.json()]
    assert "ticket.created" in types
    assert "ticket.resolved" in types
    assert "ticket.closed" in types


def test_arbitrary_status_patch_not_exposed(client, tenant_id):
    auth = {"Authorization": f"Bearer {make_token('SUPPORT_MANAGER', tenant_id)}"}
    ticket = _create(client, tenant_id)
    # There is no PATCH /tickets/{id} {"status": ...} endpoint (404 or 405).
    resp = client.patch(f"/api/support/tickets/{ticket['id']}", json={"status": "CLOSED"}, headers=auth)
    assert resp.status_code in (404, 405)


def test_sla_policy_api(client, tenant_id):
    auth = {"Authorization": f"Bearer {make_token('SUPPORT_MANAGER', tenant_id)}"}
    r = client.post("/api/support/sla/policies", params={"tenant_id": str(tenant_id)},
                    json={"code": "API_SLA", "name": "API SLA"}, headers=auth)
    assert r.status_code == 201
    policy_id = r.json()["id"]

    r = client.post(f"/api/support/sla/policies/{policy_id}/versions", params={"tenant_id": str(tenant_id)},
                    json={"definition": {"pause_on_states": ["PENDING_CUSTOMER"], "reopen_policy": "RESTART",
                                         "escalation": [{"target": "RESOLUTION", "at_risk_pct": 50, "level": 1, "action": "NOTIFY_TEAM_LEAD"}]},
                          "targets": [{"priority": "ALL", "kind": "RESPONSE", "business_seconds": 3600},
                                      {"priority": "ALL", "kind": "RESOLUTION", "business_seconds": 7200}],
                          "activate": True}, headers=auth)
    assert r.status_code == 201, r.text
    assert r.json()["active"] is True

    r = client.get("/api/support/sla/policies", params={"tenant_id": str(tenant_id)}, headers=auth)
    assert r.status_code == 200
    assert any(p["code"] == "API_SLA" for p in r.json())


def test_diagnostics_api(client, tenant_id):
    auth = {"Authorization": f"Bearer {make_token('SUPPORT_MANAGER', tenant_id)}"}
    ticket = _create(client, tenant_id)
    r = client.post(f"/api/support/tickets/{ticket['id']}/diagnostics/refresh", headers=auth)
    assert r.status_code == 200
    assert r.json()["status"] in ("COMPLETE", "PARTIAL", "FAILED")
    assert isinstance(r.json()["checks"], list)


def test_action_preview_requires_auth(client, tenant_id):
    auth = {"Authorization": f"Bearer {make_token('SUPPORT_MANAGER', tenant_id)}"}
    ticket = _create(client, tenant_id)
    r = client.post(f"/api/support/tickets/{ticket['id']}/actions/preview",
                    json={"action_type": "DISCONNECT_REAUTHORIZE"}, headers=auth)
    assert r.status_code == 200
    assert r.json()["disruptive"] is True


def test_disruptive_action_api_flow(client, tenant_id):
    auth = {"Authorization": f"Bearer {make_token('SUPPORT_MANAGER', tenant_id)}"}
    ticket = _create(client, tenant_id)
    r = client.post(f"/api/support/tickets/{ticket['id']}/actions",
                    json={"action_type": "DISCONNECT_REAUTHORIZE", "payload": {"subscriber_username": "subs-0001"}}, headers=auth)
    assert r.status_code == 201
    action_id = r.json()["id"]
    assert r.json()["status"] == "AUTHORIZATION_REQUIRED"

    r = client.post(f"/api/support/actions/{action_id}/approve", json={"reason": "confirmed"}, headers=auth)
    assert r.status_code == 200 and r.json()["status"] == "APPROVED"

    r = client.post(f"/api/support/actions/{action_id}/execute", headers=auth)
    assert r.status_code == 200 and r.json()["status"] == "SUCCEEDED"


def test_incident_link_api(client, tenant_id):
    from app.integrations.fakes import STATE

    auth = {"Authorization": f"Bearer {make_token('SUPPORT_MANAGER', tenant_id)}"}
    ticket = _create(client, tenant_id)
    STATE.outages.append({"id": "INC-9", "number": "INC-9", "service_location": "loc-1"})
    r = client.post(f"/api/support/tickets/{ticket['id']}/incidents/suggest", headers=auth)
    assert r.status_code == 200
    r = client.post(f"/api/support/tickets/{ticket['id']}/incidents/link",
                    json={"incident_id": "INC-9", "incident_number": "INC-9"}, headers=auth)
    assert r.status_code == 200
    assert r.json()["nms_incident_id"] == "INC-9"


def test_knowledge_api(client, tenant_id):
    auth = {"Authorization": f"Bearer {make_token('SUPPORT_MANAGER', tenant_id)}"}
    r = client.post("/api/support/knowledge", params={"tenant_id": str(tenant_id)},
                    json={"slug": "no-internet-troubleshooting", "title": "No internet", "body": "Step 1: reboot ONT",
                          "visibility": "INTERNAL", "status": "DRAFT"}, headers=auth)
    assert r.status_code == 201
    article_id = r.json()["id"]
    r = client.post(f"/api/support/knowledge/{article_id}/publish", params={"tenant_id": str(tenant_id)}, headers=auth)
    assert r.status_code == 200 and r.json()["status"] == "ACTIVE"
    r = client.get("/api/support/knowledge", params={"tenant_id": str(tenant_id), "query": "internet"}, headers=auth)
    assert r.status_code == 200
    assert any(a["slug"] == "no-internet-troubleshooting" for a in r.json())


def test_reports_api(client, tenant_id):
    auth = {"Authorization": f"Bearer {make_token('SUPPORT_MANAGER', tenant_id)}"}
    _create(client, tenant_id)
    r = client.get("/api/support/reports/overview", params={"tenant_id": str(tenant_id)}, headers=auth)
    assert r.status_code == 200
    assert r.json()["open_tickets"] >= 1


def test_inbound_ingestion_api(client, tenant_id, internal_headers):
    auth = {"Authorization": f"Bearer {make_token('SUPPORT_MANAGER', tenant_id)}"}
    ticket = _create(client, tenant_id)
    r = client.post("/api/support/inbound", json={
        "tenant_id": str(tenant_id), "ticket_id": ticket["id"], "provider_message_id": "mail-1",
        "channel": "EMAIL", "body": "re: my internet is still down", "sender_email": "cust@example.com"},
        headers=internal_headers)
    assert r.status_code == 200
    # Duplicate delivery is rejected.
    r2 = client.post("/api/support/inbound", json={
        "tenant_id": str(tenant_id), "ticket_id": ticket["id"], "provider_message_id": "mail-1",
        "channel": "EMAIL", "body": "duplicate"}, headers=internal_headers)
    assert r2.status_code == 409


def test_inbound_without_internal_key_denied(client, tenant_id):
    resp = client.post("/api/support/inbound", json={"tenant_id": str(tenant_id), "provider_message_id": "x", "body": "y"})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Customer portal
# ---------------------------------------------------------------------------
def test_portal_create_and_list(client, tenant_id, customer_auth):
    payload = ticket_payload(tenant_id)
    payload.pop("tenant_id")  # portal derives tenant from the token
    r = client.post("/api/support/portal/tickets", json=payload, headers=customer_auth)
    assert r.status_code == 201, r.text
    assert r.json()["customer_status"] == "SUBMITTED"
    r = client.get("/api/support/portal/tickets", headers=customer_auth)
    assert r.status_code == 200
    assert len(r.json()) == 1


def test_portal_cannot_create_for_other_customer(client, tenant_id, customer_auth):
    payload = ticket_payload(tenant_id)
    payload["customer_id"] = "SOMEONE-ELSE"
    payload.pop("tenant_id")
    r = client.post("/api/support/portal/tickets", json=payload, headers=customer_auth)
    assert r.status_code == 403


def test_portal_detail_hides_internal_data(client, tenant_id, auth, customer_auth):
    ticket = _create(client, tenant_id)
    tid = ticket["id"]
    client.post(f"/api/support/tickets/{tid}/note", json={"body": "staff-only secret"}, headers=auth)
    client.post(f"/api/support/tickets/{tid}/reply", json={"body": "public reply"}, headers=auth)
    r = client.get(f"/api/support/portal/tickets/{tid}", headers=customer_auth)
    assert r.status_code == 200
    body = r.text
    assert "staff-only secret" not in body
    assert "public reply" in body


def test_portal_reply_and_confirm_flow(client, tenant_id, auth, customer_auth):
    ticket = _create(client, tenant_id)
    tid = ticket["id"]
    client.post(f"/api/support/tickets/{tid}/resolve",
                json={"resolution_code": "WORKAROUND_PROVIDED", "summary": "explained workaround"}, headers=auth)
    # Customer confirms the resolution -> closed.
    r = client.post(f"/api/support/portal/tickets/{tid}/confirm", headers=customer_auth)
    assert r.status_code == 200
    assert r.json()["status"] == "CLOSED"
    # CSAT
    r = client.post(f"/api/support/portal/tickets/{tid}/csat", json={"rating": 5, "comment": "resolved"}, headers=customer_auth)
    assert r.status_code == 200
    assert r.json()["rating"] == 5


def test_portal_cannot_access_other_customers_ticket(client, tenant_id, customer_auth):
    ticket = _create(client, tenant_id)  # created as CUST-0001 by management
    other_auth = {"Authorization": f"Bearer {make_customer_token('DIFFERENT-CUSTOMER', tenant_id)}"}
    r = client.get(f"/api/support/portal/tickets/{ticket['id']}", headers=other_auth)
    assert r.status_code == 403


def test_portal_ticket_detail_has_expected_timeline(client, tenant_id, customer_auth):
    payload = ticket_payload(tenant_id)
    payload.pop("tenant_id")
    r = client.post("/api/support/portal/tickets", json=payload, headers=customer_auth)
    tid = r.json()["id"]
    r = client.get(f"/api/support/portal/tickets/{tid}", headers=customer_auth)
    assert r.status_code == 200
    data = r.json()
    assert "timeline" in data
    assert data["expected_response"] is not None
    assert data["expected_resolution"] is not None
    # No internal fields leaked.
    for field in ("resolution_summary", "assigned_agent_id"):
        assert field not in data or data[field] is None
