"""Performance: feedback, KPI, SLA, escalation, shifts (features 346-349, 1117)."""
from conftest import make_technician, make_work_order

import uuid


def _completed_wo(client, headers):
    wo = make_work_order(client, headers, sla_minutes=60).json()
    wid = wo["id"]
    tid = make_technician(client, headers).json()["id"]
    client.post(f"/api/workforce/v1/work-orders/{wid}/assign", headers=headers,
                json={"technician_id": tid})
    client.post(f"/api/workforce/v1/work-orders/{wid}/dispatch", headers=headers, json={})
    client.post(f"/api/workforce/v1/work-orders/{wid}/transition", headers=headers,
                json={"transition": "EN_ROUTE"})
    client.post(f"/api/workforce/v1/work-orders/{wid}/transition", headers=headers,
                json={"transition": "ARRIVED"})
    client.post(f"/api/workforce/v1/work-orders/{wid}/transition", headers=headers,
                json={"transition": "IN_PROGRESS"})
    client.post(f"/api/workforce/v1/work-orders/{wid}/complete", headers=headers, json={})
    return wid, tid


def test_feedback_and_kpi(client, manager_headers):
    wid, tid = _completed_wo(client, manager_headers)
    r = client.post(f"/api/workforce/v1/work-orders/{wid}/feedback", headers=manager_headers,
                    json={"rating": 5, "comment": "great"})
    assert r.status_code == 201
    kpi = client.get(f"/api/workforce/v1/kpis/technician/{tid}", headers=manager_headers)
    assert kpi.status_code == 200
    body = kpi.json()
    assert body["jobs_completed"] == 1
    assert body["avg_rating"] == 5.0
    assert body["productivity_score"] > 0


def test_escalation_lifecycle(client, manager_headers):
    wo = make_work_order(client, manager_headers).json()["id"]
    r = client.post("/api/workforce/v1/escalations", headers=manager_headers,
                    json={"work_order_id": wo, "level": "LEVEL_2", "reason": "customer angry"})
    assert r.status_code == 201
    eid = r.json()["id"]
    r2 = client.post(f"/api/workforce/v1/escalations/{eid}/resolve", headers=manager_headers)
    assert r2.json()["status"] == "RESOLVED"


def test_shift_scheduling(client, manager_headers):
    tid = make_technician(client, manager_headers).json()["id"]
    r = client.post("/api/workforce/v1/shifts", headers=manager_headers,
                    json={"technician_id": tid,
                          "start_time": "2026-09-01T09:00:00+00:00",
                          "end_time": "2026-09-01T17:00:00+00:00"})
    assert r.status_code == 201
    assert r.json()["status"] == "SCHEDULED"


def test_sla_evaluate_marks_completed_on_time(client, manager_headers):
    from datetime import datetime, timedelta, timezone
    from app.database import SessionLocal
    from app import models
    wid, _ = _completed_wo(client, manager_headers)
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        sla = db.query(models.FieldSLA).filter(
            models.FieldSLA.work_order_id == uuid.UUID(wid)).first()
        sla.deadline = now - timedelta(minutes=1)      # past deadline
        wo = db.query(models.WorkOrder).filter(models.WorkOrder.id == uuid.UUID(wid)).first()
        wo.completed_at = now - timedelta(minutes=10)   # completed before deadline
        db.commit()
    finally:
        db.close()
    r = client.post("/api/workforce/v1/sla/evaluate", headers=manager_headers, json={})
    assert r.json()["checked"] >= 1
    assert r.json()["on_time"] >= 1


def test_dashboard_summary(client, manager_headers):
    make_work_order(client, manager_headers)
    make_technician(client, manager_headers)
    r = client.get("/api/workforce/v1/dashboard/summary", headers=manager_headers)
    body = r.json()
    assert body["total_work_orders"] == 1
    assert body["available_technicians"] == 1
    assert body["open_work_orders"] == 1


def test_audit_trail_records(client, manager_headers):
    make_work_order(client, manager_headers)
    r = client.get("/api/workforce/v1/audit-log", headers=manager_headers)
    actions = [a["action"] for a in r.json()]
    assert "workorder.create" in actions


def test_preventive_maintenance_scheduling():
    from datetime import datetime, timezone
    from app.database import SessionLocal
    from app import tasks
    db = SessionLocal()
    try:
        from app import models
        from app.context import TenantContext
        from app.services import TechnicianService
        ctx = TenantContext(user_id="t", role="TENANT_ADMIN", tenant_id=uuid.uuid4(),
                            permissions={"*"})
        TechnicianService.create(db, ctx, {"name": "X"})
        created = tasks.schedule_preventive_maintenance(db)
        assert len(created) == 1
    finally:
        db.close()
