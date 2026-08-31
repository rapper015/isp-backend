"""API endpoint flows for the Assurance Service."""
import uuid

from conftest import make_token


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_status(client):
    resp = client.get("/status")
    assert resp.status_code == 200
    assert resp.json()["db"] is True


def test_list_services_seeded(client, platform_headers):
    resp = client.get("/api/assurance/v1/services", headers=platform_headers)
    assert resp.status_code == 200
    codes = {s["code"] for s in resp.json()}
    assert "portal" in codes and "aaa" in codes


def test_list_slis_seeded(client, platform_headers):
    resp = client.get("/api/assurance/v1/slis", headers=platform_headers)
    assert resp.status_code == 200
    codes = {s["code"] for s in resp.json()}
    assert "sli_radius_auth_success" in codes


def test_create_slo_flow(client, platform_headers):
    slis = client.get("/api/assurance/v1/slis", headers=platform_headers).json()
    sli = next(s for s in slis if s["code"] == "sli_portal_availability")
    code = f"slo-{uuid.uuid4().hex[:8]}"
    resp = client.post("/api/assurance/v1/slos", headers=platform_headers, json={
        "code": code, "name": "Portal availability SLO", "sli_id": sli["id"],
        "objective": 0.995, "window_seconds": 30 * 24 * 3600, "published": True})
    assert resp.status_code == 200, resp.text
    slo_id = resp.json()["id"]
    client.post(f"/api/assurance/v1/slos/{slo_id}/validate", headers=platform_headers)
    client.post(f"/api/assurance/v1/slos/{slo_id}/approve", headers=platform_headers)
    resp = client.post(f"/api/assurance/v1/slos/{slo_id}/activate", headers=platform_headers)
    assert resp.json()["state"] == "ACTIVE"
    resp = client.get(f"/api/assurance/v1/slos/{slo_id}/error-budget", headers=platform_headers)
    assert resp.status_code == 200
    assert "remaining_budget" in resp.json()


def test_record_measurement_via_api(client, platform_headers):
    resp = client.post("/api/assurance/v1/sli-measurements", headers=platform_headers, json={
        "sli_code": "sli_radius_auth_success", "good": 100, "total": 100})
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True


def test_internal_ingest_alert(client, internal_headers):
    resp = client.post("/internal/assurance/v1/ingest/alert", headers=internal_headers, json={
        "service": "aaa", "alert_name": "radius_down", "severity": "CRITICAL"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["state"] == "FIRING"


def test_internal_ingest_requires_key(client):
    resp = client.post("/internal/assurance/v1/ingest/alert", json={
        "service": "aaa", "alert_name": "x"})
    assert resp.status_code == 401


def test_alert_ack_resolve_via_api(client, internal_headers, platform_headers):
    created = client.post("/internal/assurance/v1/ingest/alert", headers=internal_headers, json={
        "service": "billing", "alert_name": "payments_slow", "severity": "HIGH"}).json()
    alert_id = created["alert_id"]
    resp = client.post(f"/api/assurance/v1/alerts/{alert_id}/acknowledge", headers=platform_headers)
    assert resp.json()["state"] == "ACKNOWLEDGED"
    resp = client.post(f"/api/assurance/v1/alerts/{alert_id}/resolve", headers=platform_headers)
    assert resp.json()["state"] == "RESOLVED"


def test_incident_lifecycle_via_api(client, internal_headers, platform_headers):
    created = client.post("/internal/assurance/v1/ingest/alert", headers=internal_headers, json={
        "service": "aaa", "alert_name": "auth_outage", "severity": "CRITICAL"}).json()
    alert_id = created["alert_id"]
    resp = client.post("/api/assurance/v1/incidents", headers=platform_headers, json={
        "title": "Auth outage", "severity": "CRITICAL", "alert_id": alert_id, "source": "ALERT"})
    assert resp.status_code == 200, resp.text
    incident_id = resp.json()["id"]
    for target in ("TRIAGE", "INVESTIGATING", "IDENTIFIED", "MITIGATING", "MONITORING", "RESOLVED"):
        resp = client.post(f"/api/assurance/v1/incidents/{incident_id}/transition",
                           headers=platform_headers, json={"target": target})
        assert resp.json()["state"] == target
    resp = client.get(f"/api/assurance/v1/incidents/{incident_id}", headers=platform_headers)
    assert resp.json()["state"] == "RESOLVED"


def test_incident_impact_estimate_via_api(client, platform_headers, internal_headers):
    created = client.post("/internal/assurance/v1/ingest/alert", headers=internal_headers, json={
        "service": "aaa", "alert_name": "outage", "severity": "HIGH"}).json()
    incident_id = client.post("/api/assurance/v1/incidents", headers=platform_headers, json={
        "title": "Outage", "alert_id": created["alert_id"]}).json()["id"]
    resp = client.post(f"/api/assurance/v1/incidents/{incident_id}/impact-estimate",
                       headers=platform_headers, json={"impact_kind": "INTERNET", "estimated_subscribers": 50})
    assert resp.json()["estimated"] is True
    resp = client.post(f"/api/assurance/v1/incidents/{incident_id}/impact-confirm",
                       headers=platform_headers, json={"impact_kind": "INTERNET", "confirmed_subscribers": 30})
    assert resp.json()["estimated"] is False


def test_kpi_via_api(client, platform_headers):
    resp = client.post("/api/assurance/v1/kpis", headers=platform_headers, json={
        "code": "kpi_api_test", "name": "API Test", "formula": "count(x)"})
    assert resp.status_code == 200
    resp = client.post("/api/assurance/v1/kpi-measurements", headers=platform_headers, json={
        "kpi_code": "kpi_api_test", "period_key": "2024-06-01", "value": 3})
    assert resp.status_code == 200
    resp = client.get("/api/assurance/v1/kpis", headers=platform_headers)
    assert any(k["code"] == "kpi_api_test" for k in resp.json())


def test_maintenance_via_api(client, platform_headers):
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc)
    resp = client.post("/api/assurance/v1/maintenance", headers=platform_headers, json={
        "service_id": None, "starts_at": now.isoformat(),
        "ends_at": (now + datetime.timedelta(hours=2)).isoformat(),
        "reason": "planned window"})
    assert resp.status_code == 200, resp.text
    window_id = resp.json()["id"]
    resp = client.post(f"/api/assurance/v1/maintenance/{window_id}/approve", headers=platform_headers)
    assert resp.json()["state"] == "APPROVED"


def test_synthetic_via_api(client, platform_headers):
    resp = client.post("/api/assurance/v1/synthetic", headers=platform_headers, json={
        "code": "syn-check-1", "kind": "PORTAL_AVAILABILITY", "target": "https://portal"})
    assert resp.status_code == 200, resp.text
    resp = client.post("/api/assurance/v1/synthetic/results", headers=platform_headers, json={
        "check_code": "syn-check-1", "result": "PASS", "latency_ms": 120})
    assert resp.status_code == 200


def test_postmortem_via_api(client, platform_headers, internal_headers):
    alert = client.post("/internal/assurance/v1/ingest/alert", headers=internal_headers, json={
        "service": "aaa", "alert_name": "pm", "severity": "HIGH"}).json()
    incident_id = client.post("/api/assurance/v1/incidents", headers=platform_headers, json={
        "title": "PM incident", "alert_id": alert["alert_id"]}).json()["id"]
    for target in ("TRIAGE", "INVESTIGATING", "IDENTIFIED", "MITIGATING", "MONITORING", "RESOLVED"):
        client.post(f"/api/assurance/v1/incidents/{incident_id}/transition",
                    headers=platform_headers, json={"target": target})
    client.post(f"/api/assurance/v1/incidents/{incident_id}/require-postmortem", headers=platform_headers)
    resp = client.post("/api/assurance/v1/postmortems", headers=platform_headers, json={
        "incident_id": incident_id, "summary": "Summary"})
    assert resp.status_code == 200, resp.text
    pm_id = resp.json()["postmortem_id"]
    resp = client.post(f"/api/assurance/v1/postmortems/{pm_id}/actions", headers=platform_headers, json={
        "title": "Follow up"})
    assert resp.json()["state"] == "OPEN"


def test_root_cause_via_api(client, platform_headers, internal_headers):
    alert = client.post("/internal/assurance/v1/ingest/alert", headers=internal_headers, json={
        "service": "aaa", "alert_name": "rc", "severity": "HIGH"}).json()
    incident_id = client.post("/api/assurance/v1/incidents", headers=platform_headers, json={
        "title": "RC incident", "alert_id": alert["alert_id"]}).json()["id"]
    resp = client.post(f"/api/assurance/v1/incidents/{incident_id}/root-causes", headers=platform_headers, json={
        "hypothesis": "Core router crash", "confidence": 0.4, "is_ai_suggestion": True})
    assert resp.status_code == 200, resp.text
    h_id = resp.json()["hypothesis_id"]
    client.post(f"/api/assurance/v1/root-causes/{h_id}/evidence", headers=platform_headers, json={
        "evidence_type": "TOPOLOGY_DEPENDENCY", "evidence_ref": "pop-1", "supports": True})
    resp = client.post(f"/api/assurance/v1/root-causes/{h_id}/transition", headers=platform_headers, json={
        "target": "HYPOTHESIS"})
    assert resp.json()["state"] == "HYPOTHESIS"


def test_dashboard_tenant(client, tenant_headers, internal_headers):
    client.post("/internal/assurance/v1/ingest/alert", headers=internal_headers, json={
        "service": "aaa", "alert_name": "dash", "severity": "HIGH"})
    resp = client.get("/api/assurance/v1/dashboards/tenant", headers=tenant_headers)
    assert resp.status_code == 200
    assert "firing_alerts" in resp.json()
