"""OSS Batch 3: assets, config drift, vendors, enterprise, infra, security, telemetry."""
from conftest import make_token
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _asset_payload(tenant_id, name="OLT-1", **over):
    p = {"tenant_id": str(tenant_id), "asset_type": "OLT", "name": name,
         "vendor_id": None, "model": "C6500", "serial_number": f"SN-{uuid4().hex[:8]}",
         "firmware_version": "v1.0", "site_owner": "Site-A", "category": "core"}
    p.update(over)
    return p


def test_register_and_list_asset(client, auth_headers, tenant_id):
    r = client.post("/api/oss/assets/register", json=_asset_payload(tenant_id), headers=auth_headers)
    assert r.status_code == 201
    assert r.json()["asset_type"] == "OLT"
    rl = client.get(f"/api/oss/assets?tenant_id={tenant_id}", headers=auth_headers)
    assert len(rl.json()) == 1


def test_firmware_tracking(client, auth_headers, tenant_id):
    aid = client.post("/api/oss/assets/register", json=_asset_payload(tenant_id), headers=auth_headers).json()["id"]
    r = client.post(f"/api/oss/assets/{aid}/firmware",
                    json={"tenant_id": str(tenant_id), "to_version": "v2.4"},
                    headers=auth_headers)
    assert r.json()["to"] == "v2.4"
    rl = client.get(f"/api/oss/assets?tenant_id={tenant_id}", headers=auth_headers)
    assert rl.json()[0]["firmware_version"] == "v2.4"


def test_vendor_register_and_evaluate(client, auth_headers, tenant_id):
    vid = client.post("/api/oss/vendors", json={"tenant_id": str(tenant_id), "name": "Acme",
                                                "sla_minutes": 240}, headers=auth_headers).json()["id"]
    r = client.post(f"/api/oss/vendors/{vid}/evaluate",
                    json={"tenant_id": str(tenant_id)}, headers=auth_headers)
    assert r.json()["performance_score"] == 100.0


def test_splitter_hierarchy(client, auth_headers, tenant_id):
    root = client.post("/api/oss/splitters", json={"tenant_id": str(tenant_id), "name": "Root",
                                                   "ports_total": 32}, headers=auth_headers).json()["id"]
    child = client.post("/api/oss/splitters", json={"tenant_id": str(tenant_id), "name": "Child",
                                                    "parent_id": root, "ports_total": 16},
                        headers=auth_headers).json()
    assert child["level"] == 2
    tree = client.get(f"/api/oss/splitters/tree?tenant_id={tenant_id}", headers=auth_headers).json()
    assert tree[0]["children"][0]["name"] == "Child"


def test_config_push_and_drift(client, auth_headers, tenant_id):
    aid = client.post("/api/oss/assets/register", json=_asset_payload(tenant_id), headers=auth_headers).json()["id"]
    client.post("/api/oss/config/snapshot", json={"tenant_id": str(tenant_id), "asset_id": aid,
                                                  "config": "interface 1/0/1\n", "baseline": True},
                headers=auth_headers)
    client.post("/api/oss/config/snapshot", json={"tenant_id": str(tenant_id), "asset_id": aid,
                                                  "config": "interface 1/0/1\n ip address 10.0.0.1\n",
                                                  "baseline": False}, headers=auth_headers)
    r = client.post("/api/oss/config/drift-check", json={"tenant_id": str(tenant_id)}, headers=auth_headers)
    assert len(r.json()["drifted"]) == 1
    assert r.json()["drifted"][0]["asset_name"] == "OLT-1"
    push = client.post("/api/oss/config/push", json={"tenant_id": str(tenant_id), "asset_id": aid,
                                                     "config": "new-config"}, headers=auth_headers)
    assert push.json()["status"] == "APPLIED"


def test_inventory_reconcile_drift(client, auth_headers, tenant_id):
    client.post("/api/oss/assets/register", json=_asset_payload(tenant_id), headers=auth_headers)
    r = client.post("/api/oss/inventory/reconcile",
                    json={"tenant_id": str(tenant_id), "discovered": [
                        {"name": "OLT-1", "firmware_version": "v9.9"},
                        {"name": "ROGUE-DEVICE", "serial_number": "NOPE"}]},
                    headers=auth_headers)
    assert r.json()["matched"] == 0
    assert any(m["reason"] == "FIRMWARE_MISMATCH" for m in r.json()["mismatches"])
    assert any(m["reason"] == "UNKNOWN_DEVICE" for m in r.json()["mismatches"])


def test_enterprise_sla_vpn_bandwidth(client, auth_headers, tenant_id):
    sla = client.post("/api/oss/enterprise/slas", json={"tenant_id": str(tenant_id), "customer_id": "ent-1",
                                                        "terms": {"availability_pct": 99.9}},
                      headers=auth_headers)
    assert sla.json()["status"] == "ACTIVE"
    vpn = client.post("/api/oss/enterprise/vpns", json={"tenant_id": str(tenant_id), "name": "HQ-VPN",
                                                        "customer_id": "ent-1", "vpn_type": "MPLS"},
                      headers=auth_headers)
    assert vpn.json()["vpn_type"] == "MPLS"
    bod = client.post("/api/oss/enterprise/bandwidth", json={"tenant_id": str(tenant_id),
                                                             "subscription_id": "sub-1", "boost_mbps": 200,
                                                             "duration_minutes": 60}, headers=auth_headers)
    assert bod.json()["boost_mbps"] == 200
    assert bod.json()["expires_at"] is not None


def test_capex_and_risk_heatmap(client, auth_headers, tenant_id):
    cap = client.post("/api/oss/infra/capex", json={"tenant_id": str(tenant_id), "category": "FIBER",
                                                    "area": "North", "amount": 250000}, headers=auth_headers)
    assert cap.json()["currency"] == "INR"
    r = client.post("/api/oss/infra/risk", json={"tenant_id": str(tenant_id), "scope": "North ring",
                                                 "factors": {"fault_frequency": 5, "age_years": 8,
                                                             "fiber_degraded": True, "no_redudancy": True}},
                    headers=auth_headers)
    assert r.json()["level"] == "HIGH"
    hm = client.get(f"/api/oss/infra/risk-heatmap?tenant_id={tenant_id}", headers=auth_headers)
    assert hm.json()[0]["scope"] == "North ring"


def test_ddos_detection_and_mitigation(client, auth_headers, tenant_id):
    hit = client.post("/api/oss/security/ddos/check", json={"tenant_id": str(tenant_id), "target": "gw-1",
                                                            "vector": "SYN_FLOOD", "volume_mbps": 1200,
                                                            "baseline_mbps": 100}, headers=auth_headers)
    assert hit.json()["detected"] is True
    attack_id = hit.json()["attack_id"]
    miss = client.post("/api/oss/security/ddos/check", json={"tenant_id": str(tenant_id), "target": "gw-2",
                                                             "volume_mbps": 80, "baseline_mbps": 100},
                       headers=auth_headers)
    assert miss.json()["detected"] is False
    m = client.post(f"/api/oss/security/ddos/{attack_id}/mitigate",
                    json={"tenant_id": str(tenant_id)}, headers=auth_headers)
    assert m.json()["status"] == "MITIGATED"


def test_traffic_cost_optimization(client, auth_headers, tenant_id):
    client.post("/api/oss/traffic/cost", json={"tenant_id": str(tenant_id), "route": "A->B",
                                               "volume_gb": 100, "cost": 500}, headers=auth_headers)
    client.post("/api/oss/traffic/cost", json={"tenant_id": str(tenant_id), "route": "A->C",
                                               "volume_gb": 100, "cost": 900}, headers=auth_headers)
    r = client.get(f"/api/oss/traffic/optimize?tenant_id={tenant_id}", headers=auth_headers)
    assert r.json()["recommended_route"] == "A->B"


def test_telemetry_iot_mos_rooms_properties(client, auth_headers, tenant_id):
    iot = client.post("/api/oss/telemetry/iot", json={"tenant_id": str(tenant_id), "device_id": "iot-1",
                                                      "metric": "temperature", "value": 32.5}, headers=auth_headers)
    assert iot.json()["value"] == 32.5
    mos = client.post("/api/oss/telemetry/mos", json={"tenant_id": str(tenant_id), "session_id": "s-1",
                                                      "subscriber_id": "sub-1", "score": 4.2}, headers=auth_headers)
    assert mos.json()["score"] == 4.2
    room = client.post("/api/oss/telemetry/rooms", json={"tenant_id": str(tenant_id), "room_number": "101",
                                                         "plan_mbps": 50, "applied_mbps": 25}, headers=auth_headers)
    assert room.json()["applied_mbps"] == 25
    prop = client.post("/api/oss/telemetry/properties", json={"tenant_id": str(tenant_id),
                                                              "property_name": "Grand Hotel",
                                                              "pms_system": "Oracle"}, headers=auth_headers)
    assert prop.json()["status"] == "CONNECTED"


def test_rbac_denies_auditor_write(client, tenant_id):
    auditor = {"Authorization": f"Bearer {make_token('AUDITOR', tenant_id)}"}
    r = client.post("/api/oss/assets/register", json=_asset_payload(tenant_id), headers=auditor)
    assert r.status_code == 403


def test_tenant_isolation(client, auth_headers, tenant_id):
    other_id = uuid4()
    other = {"Authorization": f"Bearer {make_token('OSS_MANAGER', other_id)}"}
    client.post("/api/oss/assets/register", json=_asset_payload(tenant_id), headers=auth_headers)
    rl = client.get(f"/api/oss/assets?tenant_id={other_id}", headers=other)
    assert rl.json() == []
