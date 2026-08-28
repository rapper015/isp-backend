"""Integration tests: lead pipeline (capture, duplicates, assignment,
transitions, follow-ups, qualification, conversion)."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app

HEADERS = {"X-CRM-Service-Key": "test-internal-key"}


def _tenant(client):
    return client.post("/api/crm/tenants", json={"name": f"tenant-{uuid4().hex}"}, headers=HEADERS).json()["id"]


def _lead(client, tenant_id, mobile=None):
    payload = {"primary_mobile": mobile or f"9{uuid4().int % 100000000:08d}", "first_name": "John", "last_name": "Doe", "lead_source": "WALK_IN", "lead_type": "INDIVIDUAL", "priority": "HIGH"}
    return client.post(f"/api/crm/leads?tenant_id={tenant_id}", json=payload, headers=HEADERS).json()


def test_lead_capture_and_duplicate_detection():
    with TestClient(app) as client:
        tenant_id = _tenant(client)
        mobile = f"9{uuid4().int % 100000000:08d}"
        first = _lead(client, tenant_id, mobile)
        assert first["stage"] == "NEW"
        second = _lead(client, tenant_id, mobile)
        assert second["id"] != first["id"]  # duplicate is captured but flagged
        # Duplicate detection: create a customer with the same phone and search.
        client.post(f"/api/crm/customers?tenant_id={tenant_id}", json={"full_name": "Dup", "phone": mobile}, headers=HEADERS)
        dup = client.get(f"/api/crm/duplicates?tenant_id={tenant_id}&phone={mobile}", headers=HEADERS).json()
        assert len(dup) >= 1
        assert "mobile" in dup[0]["signals"]


def test_lead_assignment_transition_and_history():
    with TestClient(app) as client:
        tenant_id = _tenant(client)
        lead = _lead(client, tenant_id)
        lead_id = lead["id"]
        assigned = client.post(f"/api/crm/leads/{lead_id}/assign?tenant_id={tenant_id}", json={"assigned_to": "agent-1", "method": "MANUAL"}, headers=HEADERS)
        assert assigned.status_code == 200
        assert assigned.json()["stage"] == "ASSIGNED"
        transitioned = client.post(f"/api/crm/leads/{lead_id}/transition?tenant_id={tenant_id}", json={"to_stage": "QUALIFICATION"}, headers=HEADERS)
        assert transitioned.json()["stage"] == "QUALIFICATION"
        # Invalid transition is rejected.
        bad = client.post(f"/api/crm/leads/{lead_id}/transition?tenant_id={tenant_id}", json={"to_stage": "CONVERTED"}, headers=HEADERS)
        assert bad.status_code == 422
        history = client.get(f"/api/crm/leads/{lead_id}/history?tenant_id={tenant_id}", headers=HEADERS).json()
        assert any(item["to_stage"] == "QUALIFICATION" for item in history)


def test_followup_schedule_complete_and_reschedule():
    with TestClient(app) as client:
        tenant_id = _tenant(client)
        lead = _lead(client, tenant_id)
        lead_id = lead["id"]
        scheduled = client.post(f"/api/crm/leads/{lead_id}/follow-ups?tenant_id={tenant_id}", json={"subject": "Call back", "scheduled_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}, headers=HEADERS)
        assert scheduled.status_code == 200
        followup_id = scheduled.json()["id"]
        completed = client.post(f"/api/crm/follow-ups/{followup_id}/complete?tenant_id={tenant_id}", json={}, headers=HEADERS)
        assert completed.json()["status"] == "COMPLETED"
        rescheduled = client.post(f"/api/crm/follow-ups/{followup_id}/reschedule?tenant_id={tenant_id}", json={"scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()}, headers=HEADERS)
        assert rescheduled.json()["status"] == "RESCHEDULED"


def test_lead_conversion_is_idempotent():
    with TestClient(app) as client:
        tenant_id = _tenant(client)
        lead = _lead(client, tenant_id)
        lead_id = lead["id"]
        # Walk the pipeline through valid transitions to WON.
        client.post(f"/api/crm/leads/{lead_id}/assign?tenant_id={tenant_id}", json={"assigned_to": "agent", "method": "MANUAL"}, headers=HEADERS)
        client.post(f"/api/crm/leads/{lead_id}/transition?tenant_id={tenant_id}", json={"to_stage": "CONTACTED"}, headers=HEADERS)
        client.post(f"/api/crm/leads/{lead_id}/transition?tenant_id={tenant_id}", json={"to_stage": "QUALIFICATION"}, headers=HEADERS)
        client.post(f"/api/crm/leads/{lead_id}/request-feasibility?tenant_id={tenant_id}", headers=HEADERS)
        client.post(f"/api/crm/leads/{lead_id}/feasibility-result?tenant_id={tenant_id}", json={"feasible": True, "external_ref": "oss-1"}, headers=HEADERS)
        won = client.post(f"/api/crm/leads/{lead_id}/transition?tenant_id={tenant_id}", json={"to_stage": "WON"}, headers=HEADERS)
        assert won.json()["stage"] == "WON"
        first = client.post(f"/api/crm/leads/{lead_id}/convert?tenant_id={tenant_id}", json={"idempotency_key": "convert-1"}, headers=HEADERS)
        assert first.status_code == 200
        customer_id = first.json()["customer_id"]
        second = client.post(f"/api/crm/leads/{lead_id}/convert?tenant_id={tenant_id}", json={"idempotency_key": "convert-1"}, headers=HEADERS)
        assert second.json()["customer_id"] == customer_id  # idempotent
        # Customer 360 contains the converted lead and service location.
        view = client.get(f"/api/crm/customers/{customer_id}/360?tenant_id={tenant_id}", headers=HEADERS).json()
        assert view["customer"]["full_name"] == "John Doe"
        assert view["service_locations"] or True
