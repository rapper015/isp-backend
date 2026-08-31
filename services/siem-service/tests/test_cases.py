"""Security case lifecycle, escalation, impact, breach notify (features 1414-1416, 1471-1475)."""
import uuid


def _mk_case(client, headers, **over):
    body = {"title": "Suspicious login", "category": "INCIDENT", "severity": "HIGH",
            "assignee": "soc-1", "linked_event_ids": []}
    body.update(over)
    return client.post("/api/siem/v1/cases", json=body, headers=headers)


def test_case_create(client, soc_headers):
    r = _mk_case(client, soc_headers)
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "OPEN"
    assert body["ref_id"].startswith("CASE-")
    assert body["priority_score"] == 75  # HIGH


def test_case_lifecycle_transitions(client, soc_headers):
    cid = _mk_case(client, soc_headers).json()["id"]
    r = client.post(f"/api/siem/v1/cases/{cid}/transition", headers=soc_headers,
                    json={"transition": "START_INVESTIGATION", "note": "analyzing"})
    assert r.json()["status"] == "INVESTIGATING"
    r = client.post(f"/api/siem/v1/cases/{cid}/transition", headers=soc_headers,
                    json={"transition": "CONTAIN"})
    assert r.json()["status"] == "CONTAINED"
    r = client.post(f"/api/siem/v1/cases/{cid}/transition", headers=soc_headers,
                    json={"transition": "RESOLVE"})
    assert r.json()["status"] == "RESOLVED"
    r = client.post(f"/api/siem/v1/cases/{cid}/transition", headers=soc_headers,
                    json={"transition": "CLOSE"})
    assert r.json()["status"] == "CLOSED"


def test_invalid_transition_rejected(client, soc_headers):
    cid = _mk_case(client, soc_headers).json()["id"]
    r = client.post(f"/api/siem/v1/cases/{cid}/transition", headers=soc_headers,
                    json={"transition": "RESOLVE"})
    assert r.status_code == 400  # OPEN -> RESOLVE not allowed


def test_case_timeline(client, soc_headers):
    cid = _mk_case(client, soc_headers).json()["id"]
    client.post(f"/api/siem/v1/cases/{cid}/transition", headers=soc_headers,
                json={"transition": "START_INVESTIGATION"})
    r = client.get(f"/api/siem/v1/cases/{cid}/timeline", headers=soc_headers)
    assert len(r.json()) == 2  # created + transition


def test_escalate_high_severity(client, soc_headers):
    cid = _mk_case(client, soc_headers, severity="CRITICAL").json()["id"]
    r = client.post(f"/api/siem/v1/cases/{cid}/escalate", headers=soc_headers)
    assert r.json()["escalated"] is True


def test_escalate_low_severity_does_not_flag(client, soc_headers):
    cid = _mk_case(client, soc_headers, severity="LOW").json()["id"]
    r = client.post(f"/api/siem/v1/cases/{cid}/escalate", headers=soc_headers)
    assert r.json()["escalated"] is False


def test_impact_assessment(client, soc_headers):
    cid = _mk_case(client, soc_headers, severity="HIGH").json()["id"]
    r = client.post(f"/api/siem/v1/cases/{cid}/impact", headers=soc_headers)
    body = r.json()
    assert body["impact_score"] > 0
    assert body["breach_impact"]["risk_level"] in ("HIGH", "MEDIUM", "LOW")


def test_breach_notify_and_track(client, soc_headers):
    cid = _mk_case(client, soc_headers, severity="CRITICAL").json()["id"]
    r = client.post(f"/api/siem/v1/cases/{cid}/notify", headers=soc_headers, json={
        "case_id": cid, "channel": "EMAIL", "audience": "REGULATOR"})
    assert r.json()["ref_id"]
    rl = client.get("/api/siem/v1/breach/notifications", headers=soc_headers)
    assert len(rl.json()) == 1
    assert rl.json()[0]["notification_tracked"] is True


def test_case_filtering_by_status(client, soc_headers):
    cid = _mk_case(client, soc_headers).json()["id"]
    client.post(f"/api/siem/v1/cases/{cid}/transition", headers=soc_headers,
                json={"transition": "START_INVESTIGATION"})
    r = client.get("/api/siem/v1/cases?status=OPEN", headers=soc_headers)
    assert all(c["status"] == "OPEN" for c in r.json())
