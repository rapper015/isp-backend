"""Offline-first mobile sync: idempotency, version conflicts, terminal work
orders, offline material/device commands and retry safety."""
from datetime import datetime, timedelta, timezone

import pytest

from app.services import offline_service, workorder_service

TODAY = datetime.now(timezone.utc).replace(hour=10, minute=0, second=0, microsecond=0)


def _add_days(days: int) -> datetime:
    return TODAY + timedelta(days=days)


def _assigned_wo(session, tenant_id, make_work_order, make_technician):
    wo = make_work_order()
    tech = make_technician("Offline Tech", skills=["FIBER_INSTALL", "ONT_INSTALL"],
                           certifications=[{"certification": "FIBER_SAFETY"}])
    from app.services import appointment_service

    workorder_service.validate_work_order(session, tenant_id, wo.id, actor="test")
    appointment_service.schedule(session, tenant_id, wo, window_start=_add_days(1),
                                 window_end=_add_days(1) + timedelta(hours=2), actor="test")
    wo = workorder_service.assign_work_order(session, tenant_id, wo.id, technician_id=tech.id,
                                             reason="offline test", actor="test")
    workorder_service.dispatch_work_order(session, tenant_id, wo.id, actor="test")
    session.commit()
    session.refresh(wo)
    return wo, tech


def _cmd(work_order_id, command, payload=None, *, version=None, cid=None, ts=None):
    return {
        "client_command_id": cid or f"cid-{command}-{work_order_id}",
        "command": command,
        "work_order_id": str(work_order_id),
        "payload": payload or {},
        "entity_version": version,
        "local_timestamp": ts or TODAY.isoformat(),
    }


def test_offline_checkin_idempotent(session, tenant_id, make_work_order, make_technician):
    wo, tech = _assigned_wo(session, tenant_id, make_work_order, make_technician)
    command = _cmd(wo.id, "check_in",
                   {"latitude": 28.6139, "longitude": 77.2090, "gps_accuracy_m": 15},
                   version=wo.aggregate_version, cid="offline-checkin-1")
    result = offline_service.process_offline_commands(session, tenant_id, device_ref="dev-1", commands=[command], actor="tech")
    assert result["results"][0]["status"] == "PROCESSED"

    # Retry the same command id -> idempotent, not re-executed.
    result2 = offline_service.process_offline_commands(session, tenant_id, device_ref="dev-1", commands=[command], actor="tech")
    assert result2["results"][0]["status"] == "PROCESSED"
    from app.models import VisitCheckIn

    checkins = session.query(VisitCheckIn).filter_by(work_order_id=wo.id).count()
    assert checkins == 1


def test_offline_stale_version_conflict(session, tenant_id, make_work_order, make_technician):
    wo, tech = _assigned_wo(session, tenant_id, make_work_order, make_technician)
    # Client thinks version 1 but server has moved on.
    command = _cmd(wo.id, "start_work", {}, version=1, cid="offline-stale-1")
    result = offline_service.process_offline_commands(session, tenant_id, device_ref="dev-1", commands=[command], actor="tech")
    assert result["results"][0]["status"] == "REJECTED"
    assert result["results"][0]["code"] == "version_conflict"


def test_offline_terminal_work_order_rejected(session, tenant_id, make_work_order, make_technician):
    wo, tech = _assigned_wo(session, tenant_id, make_work_order, make_technician)
    workorder_service.cancel_work_order(session, tenant_id, wo.id, reason="cancelled", actor="test")
    session.commit()
    session.refresh(wo)
    command = _cmd(wo.id, "start_work", {}, version=wo.aggregate_version, cid="offline-terminal-1")
    result = offline_service.process_offline_commands(session, tenant_id, device_ref="dev-1", commands=[command], actor="tech")
    assert result["results"][0]["status"] == "REJECTED"
    assert result["results"][0]["code"] == "terminal_work_order"


def test_offline_material_use_and_install(session, tenant_id, make_work_order, make_technician):
    wo, tech = _assigned_wo(session, tenant_id, make_work_order, make_technician)
    workorder_service.start_work(session, tenant_id, wo.id, actor="test")
    session.commit()
    session.refresh(wo)
    material_cmd = _cmd(wo.id, "material_use",
                        {"material_code": "FIBER_CONNECTOR", "quantity": 1},
                        version=wo.aggregate_version, cid="offline-mat-1")
    device_cmd = _cmd(wo.id, "install_device",
                      {"device_type": "ONT", "serial_number": "ONT-SN-9999", "mac_address": "11:22:33:44:55:66"},
                      version=wo.aggregate_version, cid="offline-dev-1")
    result = offline_service.process_offline_commands(session, tenant_id, device_ref="dev-1",
                                                      commands=[material_cmd, device_cmd], actor="tech")
    assert result["results"][0]["status"] == "PROCESSED"
    assert result["results"][1]["status"] == "PROCESSED"
    from app.models import MaterialUsage

    assert session.query(MaterialUsage).filter_by(work_order_id=wo.id).count() == 1


def test_offline_missing_work_order_rejected(session, tenant_id):
    command = {"client_command_id": "cid-no-wo", "command": "start_work", "work_order_id": None,
               "payload": {}, "entity_version": None, "local_timestamp": None}
    result = offline_service.process_offline_commands(session, tenant_id, device_ref="dev-1", commands=[command], actor="tech")
    assert result["results"][0]["status"] == "REJECTED"


def test_offline_unsupported_command_rejected(session, tenant_id, make_work_order, make_technician):
    wo, tech = _assigned_wo(session, tenant_id, make_work_order, make_technician)
    command = _cmd(wo.id, "teleport", {}, version=wo.aggregate_version, cid="offline-unsup-1")
    result = offline_service.process_offline_commands(session, tenant_id, device_ref="dev-1", commands=[command], actor="tech")
    assert result["results"][0]["status"] == "REJECTED"
    assert result["results"][0]["code"] == "unsupported"
