"""Field ops: checklist, site checks, visits, proof, handover (features 1111-1116, 1119)."""
from conftest import make_technician, make_work_order


def _mk_wo(client, headers):
    return make_work_order(client, headers, type="INSTALLATION").json()["id"]


def test_checklist_template_and_validation(client, manager_headers):
    client.post("/api/workforce/v1/checklist-templates", headers=manager_headers,
                json={"work_order_type": "INSTALLATION",
                      "items": ["ONT mounted", "Signal tested", "Customer signed"]})
    wo = _mk_wo(client, manager_headers)
    r = client.post(f"/api/workforce/v1/work-orders/{wo}/checklist/validate",
                    headers=manager_headers, json={"completed": ["ONT mounted"]})
    assert r.status_code == 422  # incomplete
    r2 = client.post(f"/api/workforce/v1/work-orders/{wo}/checklist/validate",
                     headers=manager_headers,
                     json={"completed": ["ONT mounted", "Signal tested", "Customer signed"]})
    assert r2.status_code == 200
    assert r2.json()["valid"] is True


def test_site_checks_flow(client, manager_headers):
    wo = _mk_wo(client, manager_headers)
    r = client.post(f"/api/workforce/v1/work-orders/{wo}/site-checks",
                    headers=manager_headers,
                    json={"kind": "SITE_FEASIBILITY", "passed": True, "details": {"power": "ok"}})
    assert r.status_code == 201
    r2 = client.post(f"/api/workforce/v1/work-orders/{wo}/site-checks",
                     headers=manager_headers, json={"kind": "SIGNAL", "passed": True})
    rl = client.get(f"/api/workforce/v1/work-orders/{wo}/site-checks", headers=manager_headers)
    assert len(rl.json()) == 2


def test_visit_and_proof(client, manager_headers):
    wo = _mk_wo(client, manager_headers)
    tid = make_technician(client, manager_headers).json()["id"]
    v = client.post(f"/api/workforce/v1/work-orders/{wo}/visits", headers=manager_headers,
                    json={"technician_id": tid, "visit_type": "SITE", "lat": 19.07, "lon": 72.87})
    assert v.status_code == 201
    vid = v.json()["id"]
    p = client.post(f"/api/workforce/v1/work-orders/{wo}/proof", headers=manager_headers,
                    json={"kind": "PHOTO", "evidence_key": "s3://wo/photos/inst-1.jpg",
                          "visit_id": vid})
    assert p.status_code == 201
    pl = client.get(f"/api/workforce/v1/work-orders/{wo}/proof", headers=manager_headers)
    assert len(pl.json()) == 1


def test_duplicate_evidence_key_rejected(client, manager_headers):
    wo = _mk_wo(client, manager_headers)
    client.post(f"/api/workforce/v1/work-orders/{wo}/proof", headers=manager_headers,
                json={"kind": "SIGNATURE", "evidence_key": "sig-1"})
    r = client.post(f"/api/workforce/v1/work-orders/{wo}/proof", headers=manager_headers,
                    json={"kind": "SIGNATURE", "evidence_key": "sig-1"})
    assert r.status_code == 409  # unique constraint on (tenant, evidence_key)


def test_handover(client, manager_headers):
    wo = _mk_wo(client, manager_headers)
    r = client.post(f"/api/workforce/v1/work-orders/{wo}/handover", headers=manager_headers,
                    json={"accepted_by": "Mr. Customer", "notes": "accepted service"})
    assert r.status_code == 201
    assert r.json()["signed_at"] is not None
