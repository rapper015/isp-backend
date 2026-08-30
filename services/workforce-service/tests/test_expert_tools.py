"""Workforce remote expert + failure visualization + AR overlay tests (Batch 8)."""
import uuid

from conftest import make_token


def _h(tenant_id, role="TENANT_ADMIN"):
    return {"Authorization": f"Bearer {make_token(role, tenant_id)}"}


def test_expert_assistance_session(client, tenant_id):
    h = _h(tenant_id)
    s = client.post("/api/workforce/v1/expert/sessions", headers=h, json={
        "work_order_id": "WO-2026-00000001", "expert_id": "EXP-1",
        "technician_id": "TECH-1", "channel": "VIDEO"})
    assert s.status_code == 201
    sid = s.json()["id"]
    assert s.json()["status"] == "ACTIVE"
    ended = client.post(f"/api/workforce/v1/expert/sessions/{sid}/end", headers=h)
    assert ended.json()["status"] == "ENDED"
    rl = client.get("/api/workforce/v1/expert/sessions", headers=h)
    assert len(rl.json()) == 1


def test_failure_visualization(client, tenant_id):
    h = _h(tenant_id)
    v = client.post("/api/workforce/v1/failure/visualizations", headers=h, json={
        "work_order_id": "WO-2026-00000002", "fault_type": "OPTICAL_LOSS",
        "overlay": {"marker": "ont-12", "hotspots": ["power", "signal"]}})
    assert v.status_code == 201
    vid = v.json()["id"]
    assert v.json()["rendered"] is False
    rendered = client.post(f"/api/workforce/v1/failure/visualizations/{vid}/rendered", headers=h)
    assert rendered.json()["rendered"] is True
    rl = client.get("/api/workforce/v1/failure/visualizations", headers=h)
    assert len(rl.json()) == 1


def test_smart_equipment_overlay(client, tenant_id):
    h = _h(tenant_id)
    o = client.post("/api/workforce/v1/equipment/overlays", headers=h, json={
        "work_order_id": "WO-2026-00000003", "device_id": "ONU-88FF",
        "recognized_model": "Nokia G-140W", "overlay_data": {"ports": 4}})
    assert o.status_code == 201
    assert o.json()["recognized_model"] == "Nokia G-140W"
    rl = client.get("/api/workforce/v1/equipment/overlays", headers=h)
    assert len(rl.json()) == 1


def test_expert_tenant_isolation(client, tenant_id):
    a = _h(tenant_id)
    b = _h(uuid.uuid4())
    client.post("/api/workforce/v1/expert/sessions", headers=a, json={
        "work_order_id": "WO-1", "expert_id": "EXP-1", "technician_id": "TECH-1"})
    assert client.get("/api/workforce/v1/expert/sessions", headers=b).json() == []


def test_readonly_denied_write(client, tenant_id):
    ro = _h(tenant_id, role="READ_ONLY")
    assert client.post("/api/workforce/v1/expert/sessions", headers=ro, json={
        "work_order_id": "WO-1", "expert_id": "E", "technician_id": "T"}).status_code == 403
