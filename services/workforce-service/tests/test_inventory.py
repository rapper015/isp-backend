"""Inventory integration: reserve/issue/install/consume, one device per service,
material reconciliation and reservation-confirmed event handling."""
from datetime import datetime, timedelta, timezone

import pytest

from app.domain.exceptions import ProofError, ValidationError
from app.messaging.consumers import handle_event
from app.models import DeviceInstallation, MaterialRequirement, MaterialUsage
from app.services import inventory_service, proof_service, workorder_service

TODAY = datetime.now(timezone.utc).replace(hour=10, minute=0, second=0, microsecond=0)


def _add_days(days: int) -> datetime:
    return TODAY + timedelta(days=days)


def _wo(session, tenant_id, make_work_order):
    wo = make_work_order()
    session.commit()
    session.refresh(wo)
    return wo


def test_reserve_material(session, tenant_id, make_work_order):
    wo = _wo(session, tenant_id, make_work_order)
    requirement = inventory_service.reserve_material(session, tenant_id, wo.id,
                                                     material_code="FIBER_CONNECTOR", quantity=2, actor="test")
    assert requirement.status == "RESERVED"
    session.commit()
    from app.integrations.fakes import STATE

    assert "FIBER_CONNECTOR" in STATE.inventory["reservations"]


def test_reserve_unknown_material_rejected(session, tenant_id, make_work_order):
    wo = _wo(session, tenant_id, make_work_order)
    with pytest.raises(ProofError):
        inventory_service.reserve_material(session, tenant_id, wo.id,
                                           material_code="NOT-IN-STOCK", quantity=1, actor="test")


def test_consume_material_records_usage(session, tenant_id, make_work_order):
    wo = _wo(session, tenant_id, make_work_order)
    usage = inventory_service.record_material_usage(session, tenant_id, wo.id,
                                                    material_code="FIBER_CONNECTOR", quantity=1, actor="test")
    session.commit()
    assert usage.inventory_transaction_ref.startswith("CON-")
    assert session.query(MaterialUsage).filter_by(work_order_id=wo.id).count() == 1


def test_consume_zero_quantity_rejected(session, tenant_id, make_work_order):
    wo = _wo(session, tenant_id, make_work_order)
    with pytest.raises(ValidationError):
        inventory_service.record_material_usage(session, tenant_id, wo.id,
                                                material_code="FIBER_CONNECTOR", quantity=0, actor="test")


def test_install_device(session, tenant_id, make_work_order):
    wo = _wo(session, tenant_id, make_work_order)
    installation = inventory_service.record_device_installation(
        session, tenant_id, wo.id, device_type="ONT", serial_number="ONT-SN-2001",
        mac_address="AA:BB:CC:DD:EE:01", actor="test")
    session.commit()
    assert installation.status == "INSTALLED"
    from app.integrations.fakes import STATE

    assert "ONT-SN-2001" in STATE.inventory["installations"]


def test_same_device_cannot_install_twice(session, tenant_id, make_work_order):
    wo1 = _wo(session, tenant_id, make_work_order)
    wo2 = _wo(session, tenant_id, make_work_order)
    inventory_service.record_device_installation(session, tenant_id, wo1.id, device_type="ONT",
                                                 serial_number="ONT-SN-3001", actor="test")
    session.commit()
    with pytest.raises(ProofError):
        inventory_service.record_device_installation(session, tenant_id, wo2.id, device_type="ONT",
                                                     serial_number="ONT-SN-3001", actor="test")


def test_recover_device(session, tenant_id, make_work_order):
    wo = _wo(session, tenant_id, make_work_order)
    inventory_service.record_device_installation(session, tenant_id, wo.id, device_type="ONT",
                                                 serial_number="ONT-SN-4001", actor="test")
    session.commit()
    proof_service.recover_device(session, tenant_id, wo.id, serial_number="ONT-SN-4001", actor="test")
    session.commit()
    from app.integrations.fakes import STATE

    assert "ONT-SN-4001" not in STATE.inventory["installations"]


def test_material_reconciliation_blocks_until_used(session, tenant_id, make_work_order):
    wo = _wo(session, tenant_id, make_work_order)
    # NEW_INSTALLATION requires FIBER_CONNECTOR + SPLICE; nothing used yet.
    errors = proof_service.material_reconciliation_errors(session, tenant_id, wo)
    assert any("FIBER_CONNECTOR" in e for e in errors)
    assert any("SPLICE" in e for e in errors)

    inventory_service.record_material_usage(session, tenant_id, wo.id, material_code="FIBER_CONNECTOR", quantity=1, actor="test")
    inventory_service.record_material_usage(session, tenant_id, wo.id, material_code="SPLICE", quantity=1, actor="test")
    errors = proof_service.material_reconciliation_errors(session, tenant_id, wo)
    assert errors == []


def test_reservation_confirmed_event_issues_requirement(session, tenant_id, make_work_order):
    wo = _wo(session, tenant_id, make_work_order)
    inventory_service.reserve_material(session, tenant_id, wo.id, material_code="FIBER_CONNECTOR",
                                       quantity=1, actor="test")
    session.commit()
    requirement = session.query(MaterialRequirement).filter_by(
        work_order_id=wo.id, material_code="FIBER_CONNECTOR").first()
    assert requirement is not None

    result = handle_event(session, {
        "id": "evt-reservation-1",
        "event_type": "inventory.reservation_confirmed.v1",
        "tenant_id": str(tenant_id),
        "payload": {"work_order_id": str(wo.id), "material_code": "FIBER_CONNECTOR"},
    })
    assert result["handled"] is True
    session.refresh(requirement)
    assert requirement.status == "ISSUED"


def test_duplicate_event_idempotent(session, tenant_id, make_work_order):
    wo = _wo(session, tenant_id, make_work_order)
    event = {
        "id": "evt-reservation-dup",
        "event_type": "inventory.reservation_confirmed.v1",
        "tenant_id": str(tenant_id),
        "payload": {"work_order_id": str(wo.id), "material_code": "FIBER_CONNECTOR"},
    }
    first = handle_event(session, event)
    second = handle_event(session, event)
    assert first["handled"] is True
    assert second["handled"] is False
    assert second["action"] == "duplicate"


def test_tenant_isolated_device_records(session, tenant_id, defaults, make_work_order):
    """The local serial uniqueness check is tenant-scoped; the authoritative
    inventory adapter still enforces one-device-on-one-service globally."""
    wo1 = _wo(session, tenant_id, make_work_order)
    wo2 = _wo(session, tenant_id, make_work_order)
    inventory_service.record_device_installation(session, tenant_id, wo1.id, device_type="ONT",
                                                 serial_number="ONT-T1-5001", actor="test")
    session.commit()
    # Same tenant, same serial on another work order -> blocked by the local defence.
    with pytest.raises(ProofError):
        inventory_service.record_device_installation(session, tenant_id, wo2.id, device_type="ONT",
                                                     serial_number="ONT-T1-5001", actor="test")
    assert session.query(DeviceInstallation).filter_by(tenant_id=tenant_id).count() == 1
