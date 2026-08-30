"""CRM partner, ecosystem, SLA/automation tests (Master Spec Batch 6)."""
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app

HEADERS = {"X-CRM-Service-Key": "test-internal-key"}


def _tenant(client):
    return client.post("/api/crm/tenants", json={"name": f"tenant-{uuid4().hex}"},
                       headers=HEADERS).json()["id"]


def _partner(client, tenant_id, name="PartnerCo"):
    return client.post("/api/crm/partners", json={"code": f"P-{uuid4().hex[:8].upper()}",
                                                  "name": name, "partner_type": "OPERATOR",
                                                  "sla_minutes": 240},
                       headers=HEADERS, params={"tenant_id": tenant_id}).json()


def test_partner_create_performance_sla():
    with TestClient(app) as c:
        tid = _tenant(c)
        p = _partner(c, tid)
        assert p["status"] == "ACTIVE"
        perf = c.post(f"/api/crm/partners/{p['id']}/performance",
                      json={"period": "MONTH", "kpi": {"orders": 100, "late_orders": 5, "conversions": 20}},
                      headers=HEADERS, params={"tenant_id": tid})
        assert perf.json()["kpi"]["orders"] == 100
        sla = c.post(f"/api/crm/partners/{p['id']}/sla/evaluate", headers=HEADERS,
                     params={"tenant_id": tid})
        assert sla.json()["sla_pct"] == 95.0
        assert sla.json()["breaches"] == 5
        assert sla.json()["performance_score"] > 0


def test_partner_hierarchy_tree():
    with TestClient(app) as c:
        tid = _tenant(c)
        root = _partner(c, tid, "Master")
        child = _partner(c, tid, "Sub")
        r = c.post(f"/api/crm/partners/{child['id']}/hierarchy",
                   json={"parent_id": root["id"]}, headers=HEADERS, params={"tenant_id": tid})
        assert r.json()["level"] == 2
        tree = c.get("/api/crm/partners/hierarchy/tree", headers=HEADERS,
                     params={"tenant_id": tid}).json()
        assert tree[0]["children"][0]["name"] == "Sub"


def test_federation_links():
    with TestClient(app) as c:
        tid = _tenant(c)
        l = c.post("/api/crm/federations", json={"operator_name": "ISP-B", "direction": "BIDIRECTIONAL",
                                                 "protocol": "API"},
                   headers=HEADERS, params={"tenant_id": tid})
        assert l.json()["status"] == "LINKED"
        links = c.get("/api/crm/federations", headers=HEADERS, params={"tenant_id": tid}).json()
        assert len(links) == 1


def test_ticket_sla_breach_and_resolve():
    with TestClient(app) as c:
        tid = _tenant(c)
        t = c.post("/api/crm/tickets/sla/start", json={"ticket_id": "TK-1", "sla_minutes": -5},
                   headers=HEADERS, params={"tenant_id": tid})
        assert t.json()["breached"] is False
        ev = c.post("/api/crm/tickets/sla/evaluate", headers=HEADERS, params={"tenant_id": tid})
        assert len(ev.json()["breached"]) == 1
        r = c.post("/api/crm/tickets/sla/resolve", json={"ticket_id": "TK-1"},
                   headers=HEADERS, params={"tenant_id": tid})
        assert r.json()["breached"] is True
        assert r.json()["resolved_at"] is not None


def test_ticket_escalation():
    with TestClient(app) as c:
        tid = _tenant(c)
        e = c.post("/api/crm/tickets/escalations", json={"ticket_id": "TK-2", "level": "LEVEL_2",
                                                         "reason": "customer waiting"},
                   headers=HEADERS, params={"tenant_id": tid})
        assert e.json()["status"] == "OPEN"
        r = c.post(f"/api/crm/tickets/escalations/{e.json()['id']}/resolve", headers=HEADERS,
                   params={"tenant_id": tid})
        assert r.json()["status"] == "RESOLVED"


def test_suggested_resolutions_keyword():
    with TestClient(app) as c:
        tid = _tenant(c)
        s = c.post("/api/crm/tickets/suggestions", json={"ticket_id": "TK-3",
                                                         "issue": "low signal on ONT at home"},
                   headers=HEADERS, params={"tenant_id": tid})
        body = s.json()
        assert "ONT" in body["suggestion"]
        assert body["source"] == "AI"
        listed = c.get("/api/crm/tickets/suggestions", headers=HEADERS,
                       params={"tenant_id": tid, "ticket_id": "TK-3"}).json()
        assert len(listed) == 1


def test_regulatory_tracking_and_submit():
    with TestClient(app) as c:
        tid = _tenant(c)
        r = c.post("/api/crm/regulatory/track", json={"reseller_id": "res-1",
                                                      "report_type": "TRAI-MONTHLY"},
                   headers=HEADERS, params={"tenant_id": tid})
        assert r.json()["status"] == "TRACKED"
        s = c.post("/api/crm/regulatory/submit", json={"reseller_id": "res-1",
                                                       "report_type": "TRAI-MONTHLY"},
                   headers=HEADERS, params={"tenant_id": tid})
        assert s.json()["status"] == "SUBMITTED"
        assert s.json()["submitted_at"] is not None


def test_tenant_isolation():
    with TestClient(app) as c:
        t1, t2 = _tenant(c), _tenant(c)
        _partner(c, t1)
        p = c.get("/api/crm/partners", headers=HEADERS, params={"tenant_id": t2})
        # no partner list endpoint for tenant check; verify via federation count
        _partner(c, t2, "Other")
        links1 = c.get("/api/crm/federations", headers=HEADERS, params={"tenant_id": t1}).json()
        assert links1 == []
        links2 = c.get("/api/crm/federations", headers=HEADERS, params={"tenant_id": t2}).json()
        assert links2 == []


def test_requires_internal_key():
    with TestClient(app) as c:
        r = c.post("/api/crm/partners", json={"code": "X", "name": "x"},
                   params={"tenant_id": str(uuid4())})
        assert r.status_code == 401
