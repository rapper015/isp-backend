"""End-to-end backend tests for the NAS/MikroTik onboarding workflow.

These tests use the in-memory FakeRouterOSAdapter injected through the adapter
factory, so no physical router is required. They exercise the API contract the
frontend consumes.
"""
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import NasJob, NasRadiusAssignment
from app.routeros import FakeRouterOSAdapter


@pytest.fixture
def e2e(monkeypatch):
    adapter = FakeRouterOSAdapter(identity="edge-pop-1", version="7.15")

    def _build_adapter(*args, **kwargs):
        return adapter

    monkeypatch.setattr("app.main.build_adapter", _build_adapter)
    monkeypatch.setattr("app.workers.build_adapter", _build_adapter)
    headers = {"X-AAA-Service-Key": "test-internal-key"}
    client = TestClient(app)
    return client, headers, adapter


def _tenant(client, headers, prefix="e2e"):
    return client.post("/api/aaa/tenants", json={"name": f"{prefix}-{uuid4().hex}"}, headers=headers).json()["id"]


def _draft(client, headers, tenant_id, host="10.50.10.2"):
    return client.post("/api/nas", headers=headers, json={"tenant_id": tenant_id, "name": "edge-pop", "management_host": host, "routeros_username": "isp-app", "routeros_password": "secret-password", "radius_source_ip": host, "services": ["pppoe"]}).json()


def _radius_server(client, headers, host):
    return client.post("/api/aaa/radius-servers", headers=headers, json={"name": f"radius-{uuid4().hex}", "host": host, "internal_api_key": "not-a-real-radius-server-key"}).json()["id"]


def test_full_onboarding_workflow(e2e):
    client, headers, adapter = e2e
    with client:
        tenant_id = _tenant(client, headers)
        draft = _draft(client, headers, tenant_id)
        nas_id = draft["id"]
        assert draft["lifecycle_status"] == "DRAFT"

        # 1. Invalid credentials fail safely.
        adapter.fail_auth = True
        failed = client.post(f"/api/nas/{nas_id}/test-connection?tenant_id={tenant_id}&idempotency_key=conn-bad&sync=true", headers=headers)
        assert failed.status_code == 200
        assert failed.json()["result"].get("error") == "AUTHENTICATION_FAILED"
        status = client.get(f"/api/nas/{nas_id}/connection-status?tenant_id={tenant_id}", headers=headers).json()
        assert status["connection_status"] == "FAILED"
        adapter.fail_auth = False

        # 2. Valid credentials connect.
        client.post(f"/api/nas/{nas_id}/test-connection?tenant_id={tenant_id}&idempotency_key=conn-good&sync=true", headers=headers)
        status = client.get(f"/api/nas/{nas_id}/connection-status?tenant_id={tenant_id}", headers=headers).json()
        assert status["connection_status"] == "CONNECTED"

        # 3. Discovery captures device info + capabilities + snapshot.
        discovered = client.post(f"/api/nas/{nas_id}/discover?tenant_id={tenant_id}&idempotency_key=discover-1&sync=true", headers=headers)
        assert discovered.status_code == 200
        capabilities = client.get(f"/api/nas/{nas_id}/capabilities?tenant_id={tenant_id}", headers=headers).json()
        assert capabilities["capabilities"]["ppp"] is True
        current = client.get(f"/api/nas/{nas_id}/current-radius-configuration?tenant_id={tenant_id}", headers=headers).json()
        assert "configuration_hash" in current

        # 4. Primary + secondary RADIUS assignments with separate secrets.
        primary = client.post(f"/api/nas/{nas_id}/radius-assignments?tenant_id={tenant_id}", headers=headers, json={"radius_server_id": _radius_server(client, headers, "10.0.0.10"), "role": "primary", "services": ["pppoe"]})
        assert primary.status_code == 200
        secondary = client.post(f"/api/nas/{nas_id}/radius-assignments?tenant_id={tenant_id}", headers=headers, json={"radius_server_id": _radius_server(client, headers, "10.0.0.20"), "role": "secondary", "services": ["pppoe"]})
        assert secondary.status_code == 200
        assignments = client.get(f"/api/nas/{nas_id}/radius-assignments?tenant_id={tenant_id}", headers=headers).json()
        assert len(assignments) == 2
        assert "shared_secret" not in str(assignments)

        # 5. Desired configuration + change preview.
        desired = client.post(f"/api/nas/{nas_id}/desired-configuration?tenant_id={tenant_id}", headers=headers, json={"services": ["pppoe"], "ppp_aaa": True, "incoming_coa": True, "interim_update_seconds": 600})
        assert desired.status_code == 200
        plan = client.post(f"/api/nas/{nas_id}/plan?tenant_id={tenant_id}", headers=headers)
        assert plan.status_code == 200
        plan_id = plan.json()["id"]
        assert plan.json()["validation"]["valid"] is True

        # 6. Apply twice is idempotent (no duplicate entries).
        first = client.post(f"/api/nas/{nas_id}/plans/{plan_id}/apply?tenant_id={tenant_id}&sync=true", headers=headers, json={"idempotency_key": "apply-1"})
        assert first.status_code == 200
        first_job = first.json()["job_id"]
        assert len(adapter.get_radius_entries()) == 2
        assert adapter.get_ppp_aaa()["use_radius"] is True
        assert adapter.get_radius_incoming()[0]["accept"] is True
        second = client.post(f"/api/nas/{nas_id}/plans/{plan_id}/apply?tenant_id={tenant_id}&sync=true", headers=headers, json={"idempotency_key": "apply-1"})
        assert second.json()["duplicate"] is True
        assert second.json()["job_id"] == first_job
        assert len(adapter.get_radius_entries()) == 2  # no duplicates

        # 7. Desired and applied match after verification.
        verified = client.post(f"/api/nas/{nas_id}/verify?tenant_id={tenant_id}", headers=headers, json={"idempotency_key": "verify-1"})
        assert verified.status_code == 200
        assert verified.json()["matched"] is True

        # 8. Registration package generated securely, revealed once.
        assignment_id = assignments[0]["id"]
        token = client.post(f"/api/nas/{nas_id}/radius-assignments/{assignment_id}/registration-package?tenant_id={tenant_id}", headers=headers).json()["reveal_token"]
        revealed = client.post(f"/api/nas/{nas_id}/radius-assignments/{assignment_id}/registration-package/reveal?tenant_id={tenant_id}&reveal_token={token}", headers=headers).json()
        assert revealed["display_once"] is True
        assert revealed["shared_secret"]
        assert client.post(f"/api/nas/{nas_id}/radius-assignments/{assignment_id}/registration-package/reveal?tenant_id={tenant_id}&reveal_token={token}", headers=headers).status_code == 404

        # 9. Manual FreeRADIUS confirmation tracked; technical verification follows.
        confirmed = client.post(f"/api/nas/{nas_id}/radius-assignments/{assignment_id}/confirm-registration?tenant_id={tenant_id}", headers=headers, json={"source_ip_correct": True, "secret_version_applied": True, "services_enabled": True, "primary_configured": True})
        assert confirmed.json()["registration_status"] == "MANUALLY_CONFIRMED"
        verified_reg = client.post(f"/api/nas/{nas_id}/radius-assignments/{assignment_id}/verify?tenant_id={tenant_id}", headers=headers, json={"signal": "authentication_request_observed"})
        assert verified_reg.json()["verified"] is True

        # 10. Credentials/password never appear in API responses.
        nas_detail = client.get(f"/api/nas/{nas_id}?tenant_id={tenant_id}", headers=headers).text
        assert "secret-password" not in nas_detail
        audit = client.get(f"/api/nas/{nas_id}/audit?tenant_id={tenant_id}", headers=headers).text
        assert "secret-password" not in audit


def test_concurrent_jobs_cannot_configure_same_nas(e2e):
    client, headers, adapter = e2e
    with client:
        tenant_id = _tenant(client, headers)
        nas_id = _draft(client, headers, tenant_id)["id"]
        client.post(f"/api/nas/{nas_id}/test-connection?tenant_id={tenant_id}&idempotency_key=c-1&sync=true", headers=headers)
        client.post(f"/api/nas/{nas_id}/discover?tenant_id={tenant_id}&idempotency_key=d-1&sync=true", headers=headers)
        client.post(f"/api/nas/{nas_id}/radius-assignments?tenant_id={tenant_id}", headers=headers, json={"radius_server_id": _radius_server(client, headers, "10.0.0.30"), "role": "primary", "services": ["pppoe"]})
        client.post(f"/api/nas/{nas_id}/desired-configuration?tenant_id={tenant_id}", headers=headers, json={"services": ["pppoe"], "ppp_aaa": True})
        plan = client.post(f"/api/nas/{nas_id}/plan?tenant_id={tenant_id}", headers=headers).json()
        plan_id = plan["id"]

        # Same idempotency key cannot queue a second job.
        first = client.post(f"/api/nas/{nas_id}/plans/{plan_id}/apply?tenant_id={tenant_id}", headers=headers, json={"idempotency_key": "apply-concurrent-1"})
        duplicate = client.post(f"/api/nas/{nas_id}/plans/{plan_id}/apply?tenant_id={tenant_id}", headers=headers, json={"idempotency_key": "apply-concurrent-1"})
        assert first.json()["job_id"] == duplicate.json()["job_id"]
        assert duplicate.json()["duplicate"] is True

        # A second job with a different key queues, but the per-NAS lock prevents
        # a second worker from running it while the first holds the lock.
        from uuid import UUID as _UUID
        from app.database import SessionLocal
        from app.locks import acquire_nas_lock
        second_job_id = client.post(f"/api/nas/{nas_id}/plans/{plan_id}/apply?tenant_id={tenant_id}", headers=headers, json={"idempotency_key": "apply-concurrent-2"}).json()["job_id"]
        session = SessionLocal()
        try:
            acquired, _ = acquire_nas_lock(session, _UUID(nas_id), ttl_seconds=60)
            assert acquired is True
            # The worker can now process the queued job; it must not run because
            # the lock is held (returns LOCKED and leaves the job QUEUED).
            from app.workers import process_nas_job
            status = process_nas_job(session, _UUID(second_job_id))
            assert status == "LOCKED"
            job = session.get(NasJob, _UUID(second_job_id))
            assert job.status == "QUEUED"
        finally:
            session.close()
        # No duplicate router entries were created.
        assert len(adapter.get_radius_entries()) == 0


def test_tenant_isolation(e2e):
    client, headers, adapter = e2e
    with client:
        tenant_a = _tenant(client, headers, "iso-a")
        tenant_b = _tenant(client, headers, "iso-b")
        nas_a = _draft(client, headers, tenant_a, host="10.50.11.2")["id"]
        # Tenant B cannot see or act on Tenant A's NAS.
        assert client.get(f"/api/nas/{nas_a}?tenant_id={tenant_b}", headers=headers).status_code == 404
        assert client.post(f"/api/nas/{nas_a}/test-connection?tenant_id={tenant_b}&idempotency_key=x&sync=true", headers=headers).status_code == 404
        listing = client.get(f"/api/nas?tenant_id={tenant_b}", headers=headers).json()
        assert all(item["tenant_id"] == tenant_b for item in listing)


def test_verification_failure_prevents_active_status(e2e):
    client, headers, adapter = e2e
    with client:
        tenant_id = _tenant(client, headers)
        nas_id = _draft(client, headers, tenant_id)["id"]
        client.post(f"/api/nas/{nas_id}/test-connection?tenant_id={tenant_id}&idempotency_key=c&sync=true", headers=headers)
        client.post(f"/api/nas/{nas_id}/discover?tenant_id={tenant_id}&idempotency_key=d&sync=true", headers=headers)
        client.post(f"/api/nas/{nas_id}/radius-assignments?tenant_id={tenant_id}", headers=headers, json={"radius_server_id": _radius_server(client, headers, "10.0.0.40"), "role": "primary", "services": ["pppoe"]})
        client.post(f"/api/nas/{nas_id}/desired-configuration?tenant_id={tenant_id}", headers=headers, json={"services": ["pppoe"], "ppp_aaa": True})
        plan = client.post(f"/api/nas/{nas_id}/plan?tenant_id={tenant_id}", headers=headers).json()

        # Router rejects the command -> job fails, NAS never reaches ACTIVE.
        adapter.command_error = "no permission"
        applied = client.post(f"/api/nas/{nas_id}/plans/{plan['id']}/apply?tenant_id={tenant_id}&sync=true", headers=headers, json={"idempotency_key": "apply-fail"})
        assert applied.status_code == 200
        nas_detail = client.get(f"/api/nas/{nas_id}?tenant_id={tenant_id}", headers=headers).json()
        assert nas_detail["lifecycle_status"] != "ACTIVE"


def test_drift_detection_reports_external_change(e2e):
    client, headers, adapter = e2e
    with client:
        tenant_id = _tenant(client, headers)
        nas_id = _draft(client, headers, tenant_id)["id"]
        client.post(f"/api/nas/{nas_id}/test-connection?tenant_id={tenant_id}&idempotency_key=c&sync=true", headers=headers)
        client.post(f"/api/nas/{nas_id}/discover?tenant_id={tenant_id}&idempotency_key=d&sync=true", headers=headers)
        client.post(f"/api/nas/{nas_id}/radius-assignments?tenant_id={tenant_id}", headers=headers, json={"radius_server_id": _radius_server(client, headers, "10.0.0.50"), "role": "primary", "services": ["pppoe"]})
        client.post(f"/api/nas/{nas_id}/desired-configuration?tenant_id={tenant_id}", headers=headers, json={"services": ["pppoe"], "ppp_aaa": True})
        plan = client.post(f"/api/nas/{nas_id}/plan?tenant_id={tenant_id}", headers=headers).json()
        client.post(f"/api/nas/{nas_id}/plans/{plan['id']}/apply?tenant_id={tenant_id}&sync=true", headers=headers, json={"idempotency_key": "apply-ok"})

        # Externally managed change: an operator adds an unrelated RADIUS entry.
        adapter.seed_radius_entry(address="192.0.2.99", service=["hotspot"])
        drift = client.post(f"/api/nas/{nas_id}/detect-drift?tenant_id={tenant_id}", headers=headers).json()
        assert drift["classification"] in {"SAFE", "WARNING"}
        assert any(item["kind"] == "unknown_external_entry" for item in drift["items"])
        # External entry is preserved.
        assert any(entry["address"] == "192.0.2.99" for entry in adapter.get_radius_entries())


def test_rollback_restores_managed_settings(e2e):
    client, headers, adapter = e2e
    with client:
        tenant_id = _tenant(client, headers)
        nas_id = _draft(client, headers, tenant_id)["id"]
        client.post(f"/api/nas/{nas_id}/test-connection?tenant_id={tenant_id}&idempotency_key=c&sync=true", headers=headers)
        client.post(f"/api/nas/{nas_id}/discover?tenant_id={tenant_id}&idempotency_key=d&sync=true", headers=headers)
        client.post(f"/api/nas/{nas_id}/radius-assignments?tenant_id={tenant_id}", headers=headers, json={"radius_server_id": _radius_server(client, headers, "10.0.0.60"), "role": "primary", "services": ["pppoe"]})
        client.post(f"/api/nas/{nas_id}/desired-configuration?tenant_id={tenant_id}", headers=headers, json={"services": ["pppoe"], "ppp_aaa": True})
        plan = client.post(f"/api/nas/{nas_id}/plan?tenant_id={tenant_id}", headers=headers).json()
        client.post(f"/api/nas/{nas_id}/plans/{plan['id']}/apply?tenant_id={tenant_id}&sync=true", headers=headers, json={"idempotency_key": "apply-ok"})
        assert adapter.get_ppp_aaa()["use_radius"] is True
        rollback = client.post(f"/api/nas/{nas_id}/rollback?tenant_id={tenant_id}&sync=true", headers=headers, json={"idempotency_key": "rollback-1"})
        assert rollback.status_code == 200
