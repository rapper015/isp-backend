"""End-to-end new-installation scenario: OSS order -> work order -> schedule ->
assign -> dispatch -> field execution (checklist/proof/material/device) ->
QA approval -> completed work order, with SLA, events and outbox intact."""
from datetime import datetime, timedelta, timezone

import pytest

from app.messaging.consumers import handle_event
from app.models import OutboxEvent, WorkOrder
from app.services import (
    checklist_service,
    proof_service,
    qa_service,
    technician_service,
    workorder_service,
)
from app.services.audit_service import work_order_events

TODAY = datetime.now(timezone.utc).replace(hour=10, minute=0, second=0, microsecond=0)


def _add_days(days: int) -> datetime:
    return TODAY + timedelta(days=days)


CHECKLIST = {
    "VERIFY_CUSTOMER": True, "INSPECT_SITE": True, "INSTALL_CABLE": True, "INSTALL_ONT": True,
    "SCAN_SERIAL": "ONT-SN-E2E1", "RECORD_MAC": "AA:BB:CC:DD:EE:E1", "OPTICAL_READING": -17.5,
    "SERVICE_TEST": 120, "PHOTO_INSTALLATION": {"file_ref": "e2e-photo"}, "CUSTOMER_ACK": {"file_ref": "e2e-ack"},
    "MATERIALS_USED": "fiber",
}


def test_full_new_installation_flow(session, tenant_id, defaults, make_technician):
    # 1. OSS tells workforce a field visit is required.
    result = handle_event(session, {
        "id": "e2e-oss-event",
        "event_type": "oss.order.field_work_required.v1",
        "tenant_id": str(tenant_id),
        "payload": {
            "order_id": "ORD-E2E", "order_number": "ORD-E2E", "order_type": "NEW_CONNECTION",
            "customer_id": "CUST-E2E", "service_subscription_id": "SUB-E2E", "service_location_id": "loc-e2e",
            "latitude": 28.6139, "longitude": 77.2090,
        },
    })
    assert result["handled"] is True
    wo = session.scalar(__import__("sqlalchemy").select(WorkOrder).where(WorkOrder.oss_order_id == "ORD-E2E"))
    assert wo is not None
    assert wo.work_order_type == "NEW_INSTALLATION"
    assert wo.field_sla_status == "ACTIVE"

    # 2. Validate + schedule.
    wo = workorder_service.validate_work_order(session, tenant_id, wo.id, actor="test")
    assert wo.status == "READY_FOR_SCHEDULING"
    from app.services import appointment_service

    appointment = appointment_service.schedule(session, tenant_id, wo, window_start=_add_days(1),
                                               window_end=_add_days(1) + timedelta(hours=3),
                                               customer_preferred=True, actor="test")
    assert appointment.status == "CUSTOMER_CONFIRMATION_PENDING"
    assert wo.status == "SCHEDULED"

    # 3. Customer confirms appointment.
    appointment = appointment_service.confirm(session, tenant_id, appointment.id, actor="customer")
    assert appointment.status == "CONFIRMED"

    # 4. Assign + dispatch a qualified technician.
    technician = make_technician("E2E Tech", skills=["FIBER_INSTALL", "ONT_INSTALL"],
                                 certifications=[{"certification": "FIBER_SAFETY"}])
    wo = workorder_service.assign_work_order(session, tenant_id, wo.id, technician_id=technician.id,
                                             reason="e2e assignment", actor="test")
    assert str(wo.assigned_technician_id) == str(technician.id)
    wo = workorder_service.dispatch_work_order(session, tenant_id, wo.id, actor="test")
    assert wo.dispatch_state == "DISPATCHED"
    assert wo.status == "DISPATCHED"

    # 5. Technician executes on site.
    wo = workorder_service.start_travel(session, tenant_id, wo.id, actor="test")
    wo = workorder_service.check_in_work_order(
        session, tenant_id, wo.id, technician_id=technician.id,
        payload={"latitude": 28.6139, "longitude": 77.2090, "gps_accuracy_m": 12}, actor="test")
    assert wo.status == "ARRIVED"
    wo = workorder_service.start_work(session, tenant_id, wo.id, actor="test")
    assert wo.status == "IN_PROGRESS"

    # 6. Checklist, proof, acknowledgement, materials, device.
    checklist_service.submit_responses(session, tenant_id, wo.id, responses=CHECKLIST, submitted_by="e2e-tech")
    proof_service.add_proof(session, tenant_id, wo.id, evidence_key="e2e-proof-photo", evidence_type="PHOTOGRAPH",
                            file_ref="e2e-photo", checksum="x", capture_timestamp=TODAY, actor="test")
    proof_service.add_proof(session, tenant_id, wo.id, evidence_key="e2e-proof-serial", evidence_type="SERIAL_NUMBER",
                            file_ref="e2e-photo", checksum="x", capture_timestamp=TODAY, actor="test")
    proof_service.add_proof(session, tenant_id, wo.id, evidence_key="e2e-proof-ack", evidence_type="CUSTOMER_ACKNOWLEDGEMENT",
                            file_ref="e2e-ack", checksum="x", capture_timestamp=TODAY, actor="test")
    proof_service.record_customer_acknowledgement(session, tenant_id, wo.id, method="CUSTOMER_OTP",
                                                  masked_recipient="cus***e2", consent_text_version="v2",
                                                  result="CONFIRMED", actor="test")
    from app.services import inventory_service

    inventory_service.record_material_usage(session, tenant_id, wo.id, material_code="FIBER_CONNECTOR", quantity=1, actor="test")
    inventory_service.record_material_usage(session, tenant_id, wo.id, material_code="SPLICE", quantity=1, actor="test")
    inventory_service.record_device_installation(session, tenant_id, wo.id, device_type="ONT",
                                                 serial_number="ONT-SN-E2E1", mac_address="AA:BB:CC:DD:EE:E1", actor="test")

    # 7. Finish execution, submit for verification, QA approve.
    wo = workorder_service.finish_execution(session, tenant_id, wo.id, actor="test")
    assert wo.status == "EXECUTION_COMPLETED"
    wo = workorder_service.submit_for_verification(session, tenant_id, wo.id, actor="test")
    assert wo.status == "VERIFICATION_PENDING"
    review = qa_service.approve_review(session, tenant_id, wo.id, reviewer="qa-e2e")
    session.commit()
    assert review.state == "APPROVED"
    assert wo.status == "COMPLETED"

    # 8. SLA completed (not breached), events + outbox intact.
    assert wo.field_sla_status == "COMPLETED"
    events = list(work_order_events(session, wo.id))
    assert any(e.event_type == "work_order.completed" for e in events)
    assert any(e.event_type == "work_order.created" for e in events)
    from sqlalchemy import select

    published = [o.event_type for o in session.scalars(
        select(OutboxEvent).where(OutboxEvent.tenant_id == tenant_id))]
    assert "workforce.work_order.completed.v1" in published
    assert "workforce.work_order.scheduled.v1" in published
    assert "workforce.work_order.technician_arrived.v1" in published
    assert "workforce.inventory.device_installed.v1" in published

    # 9. Technician returns to AVAILABLE.
    technician = technician_service.get_technician_or_404(session, tenant_id, technician.id)
    assert technician.operational_status == "AVAILABLE"


def test_failed_installation_flow(session, tenant_id, defaults, make_technician):
    """A fault that blocks execution records a blocker and fails cleanly."""
    from app.services import appointment_service

    wo = workorder_service.create_work_order(session, tenant_id, work_order_type="FAULT_REPAIR",
                                             customer_id="CUST-E2E-F", service_location_id="loc-f",
                                             source_channel="API", actor="test")
    technician = make_technician("Fail Tech", skills=["FIBER_INSTALL"],
                                 certifications=[{"certification": "FIBER_SAFETY"}])
    session.commit()
    workorder_service.validate_work_order(session, tenant_id, wo.id, actor="test")
    appointment_service.schedule(session, tenant_id, wo, window_start=_add_days(1),
                                 window_end=_add_days(1) + timedelta(hours=2), actor="test")
    workorder_service.assign_work_order(session, tenant_id, wo.id, technician_id=technician.id,
                                        reason="e2e fail", actor="test")
    workorder_service.dispatch_work_order(session, tenant_id, wo.id, actor="test")
    workorder_service.start_work(session, tenant_id, wo.id, actor="test")
    wo = workorder_service.record_blocker(session, tenant_id, wo.id, blocker_type="ACCESS_DENIED",
                                          reason="premises locked", actor="test")
    assert wo.status == "BLOCKED"
    wo = workorder_service.fail_work_order(session, tenant_id, wo.id, reason="unable to access", actor="test")
    assert wo.status == "FAILED"
