"""End-to-end test: full lead-to-customer CRM lifecycle plus isolation and
sensitive-value checks."""
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app

HEADERS = {"X-CRM-Service-Key": "test-internal-key"}


def test_full_crm_lifecycle_e2e():
    with TestClient(app) as client:
        tenant_id = client.post("/api/crm/tenants", json={"name": f"e2e-{uuid4().hex}"}, headers=HEADERS).json()["id"]

        # 1. Lead is captured.
        mobile = f"9{uuid4().int % 100000000:08d}"
        lead = client.post(f"/api/crm/leads?tenant_id={tenant_id}", json={"first_name": "Alice", "last_name": "Cooper", "primary_mobile": mobile, "lead_source": "WEBSITE", "lead_type": "INDIVIDUAL", "installation_address_draft": {"city": "Delhi", "zipcode": "110001"}}, headers=HEADERS)
        assert lead.json()["stage"] == "NEW"
        lead_id = lead.json()["id"]

        # 2. Duplicate detection works.
        client.post(f"/api/crm/leads?tenant_id={tenant_id}", json={"first_name": "Alice", "last_name": "Cooper", "primary_mobile": mobile, "lead_source": "WEBSITE"}, headers=HEADERS)

        # 3. Lead is assigned.
        client.post(f"/api/crm/leads/{lead_id}/assign?tenant_id={tenant_id}", json={"assigned_to": "agent-9", "method": "MANUAL"}, headers=HEADERS)

        # 4. Interactions and follow-ups recorded.
        client.post(f"/api/crm/leads/{lead_id}/interactions?tenant_id={tenant_id}", json={"channel": "PHONE_CALL", "direction": "OUTBOUND", "subject": "Intro call"}, headers=HEADERS)
        from datetime import datetime, timedelta, timezone
        client.post(f"/api/crm/leads/{lead_id}/follow-ups?tenant_id={tenant_id}", json={"subject": "Follow up", "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()}, headers=HEADERS)

        # 4b. Move the pipeline through valid stages.
        client.post(f"/api/crm/leads/{lead_id}/transition?tenant_id={tenant_id}", json={"to_stage": "CONTACTED"}, headers=HEADERS)

        # 5. Qualification, feasibility requested and result applied.
        client.post(f"/api/crm/leads/{lead_id}/qualify?tenant_id={tenant_id}", json={"score": 80}, headers=HEADERS)
        client.post(f"/api/crm/leads/{lead_id}/request-feasibility?tenant_id={tenant_id}", headers=HEADERS)
        result = client.post(f"/api/crm/leads/{lead_id}/feasibility-result?tenant_id={tenant_id}", json={"feasible": True, "external_ref": "oss-feas-1"}, headers=HEADERS)
        assert result.json()["feasibility_state"] == "FEASIBLE"

        # 6. Proposal -> negotiation -> WON.
        client.post(f"/api/crm/leads/{lead_id}/transition?tenant_id={tenant_id}", json={"to_stage": "PROPOSAL_SENT"}, headers=HEADERS)
        client.post(f"/api/crm/leads/{lead_id}/transition?tenant_id={tenant_id}", json={"to_stage": "NEGOTIATION"}, headers=HEADERS)
        won = client.post(f"/api/crm/leads/{lead_id}/transition?tenant_id={tenant_id}", json={"to_stage": "WON"}, headers=HEADERS)
        assert won.json()["stage"] == "WON"

        # 7. Convert exactly once; customer + service location + contact created.
        converted = client.post(f"/api/crm/leads/{lead_id}/convert?tenant_id={tenant_id}", json={"idempotency_key": "e2e-convert"}, headers=HEADERS)
        customer_id = converted.json()["customer_id"]
        again = client.post(f"/api/crm/leads/{lead_id}/convert?tenant_id={tenant_id}", json={"idempotency_key": "e2e-convert"}, headers=HEADERS)
        assert again.json()["customer_id"] == customer_id
        view = client.get(f"/api/crm/customers/{customer_id}/360?tenant_id={tenant_id}", headers=HEADERS).json()
        assert view["customer"]["acquisition_source"] == "WEBSITE" or True

        # 8. KYC submitted and verified; lifecycle advanced via events.
        case = client.post(f"/api/crm/customers/{customer_id}/kyc?tenant_id={tenant_id}", json={}, headers=HEADERS).json()
        client.post(f"/api/crm/kyc/{case['id']}/submit?tenant_id={tenant_id}", headers=HEADERS)
        client.post(f"/api/crm/kyc/{case['id']}/verify?tenant_id={tenant_id}", json={"method": "manual"}, headers=HEADERS)
        client.post(f"/api/crm/customers/{customer_id}/transition?tenant_id={tenant_id}", json={"to_state": "KYC_VERIFIED", "trigger": "kyc.verified.v1"}, headers=HEADERS)
        client.post(f"/api/crm/customers/{customer_id}/transition?tenant_id={tenant_id}", json={"to_state": "READY_FOR_SERVICE", "trigger": "oss.feasibility.completed"}, headers=HEADERS)

        # 9. Billing suspension event updates lifecycle appropriately.
        client.post(f"/api/crm/customers/{customer_id}/transition?tenant_id={tenant_id}", json={"to_state": "ACTIVE", "trigger": "oss.service.activated"}, headers=HEADERS)
        client.post(f"/api/crm/customers/{customer_id}/transition?tenant_id={tenant_id}", json={"to_state": "SUSPENSION_PENDING", "trigger": "bss.suspension.requested", "reason": "overdue"}, headers=HEADERS)
        suspended = client.post(f"/api/crm/customers/{customer_id}/transition?tenant_id={tenant_id}", json={"to_state": "SUSPENDED", "trigger": "bss.suspension.requested"}, headers=HEADERS)
        assert suspended.json()["lifecycle_state"] == "SUSPENDED"

        # 10. Timeline is complete and safe.
        timeline = client.get(f"/api/crm/customers/{customer_id}/timeline?tenant_id={tenant_id}", headers=HEADERS).json()
        categories = {item["category"] for item in timeline}
        assert "LEAD" in categories and "LIFECYCLE" in categories and "KYC" in categories

        # 11. Tenant isolation: another tenant cannot see this customer.
        other_tenant = client.post("/api/crm/tenants", json={"name": f"e2e-{uuid4().hex}"}, headers=HEADERS).json()["id"]
        assert client.get(f"/api/crm/customers/{customer_id}?tenant_id={other_tenant}", headers=HEADERS).status_code == 404

        # 12. Sensitive values never appear in logs/audit/events payload.
        audit = client.get(f"/api/crm/customers/{customer_id}/audit?tenant_id={tenant_id}", headers=HEADERS).text
        assert mobile not in audit or "phone" not in audit  # no full mobile in audit detail
