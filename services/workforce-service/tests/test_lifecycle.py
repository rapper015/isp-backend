"""Work-order lifecycle: validated transitions, assignment, dispatch, field
execution, blockers, QA, completion and cancellation."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.domain.exceptions import ChecklistError, ProofError, QAError, ValidationError
from app.models import Appointment, ProofOfWork, QualityReview, WorkOrder, WorkOrderAssignment
from app.services import (
    checklist_service,
    proof_service,
    qa_service,
    workorder_service,
)

TODAY = datetime.now(timezone.utc).replace(hour=10, minute=0, second=0, microsecond=0)


def _add_days(days: int) -> datetime:
    return TODAY + timedelta(days=days)


NEW_INSTALL_CHECKLIST = {
    "VERIFY_CUSTOMER": True,
    "INSPECT_SITE": True,
    "INSTALL_CABLE": True,
    "INSTALL_ONT": True,
    "SCAN_SERIAL": "ONT-SN-1001",
    "RECORD_MAC": "AA:BB:CC:DD:EE:FF",
    "OPTICAL_READING": -18.5,
    "SERVICE_TEST": 100,
    "PHOTO_INSTALLATION": {"file_ref": "file-proof-1"},
    "CUSTOMER_ACK": {"file_ref": "file-proof-ack"},
    "MATERIALS_USED": "2m fiber, 1 splice",
}


def _schedule_and_assign(session, tenant_id, wo, technician, *, customer_preferred=False):
    appointment = workorder_service.validate_work_order(session, tenant_id, wo.id, actor="test")
    from app.services import appointment_service

    appointment = appointment_service.schedule(session, tenant_id, appointment,
                                               window_start=_add_days(1), window_end=_add_days(1) + timedelta(hours=2),
                                               customer_preferred=customer_preferred, actor="test")
    wo = workorder_service.assign_work_order(session, tenant_id, wo.id, technician_id=technician.id,
                                             reason="test assignment", actor="test")
    return appointment, wo


def _complete_execution(session, tenant_id, wo, technician):
    # Unique device identifiers per work order to respect global serial/MAC
    # uniqueness across the shared session-scoped test database.
    suffix = str(wo.id)[:8].upper()
    serial = f"ONT-SN-{suffix}"
    mac = f"AA:BB:CC:DD:EE:{suffix[:2]}"
    workorder_service.start_travel(session, tenant_id, wo.id, actor="test")
    workorder_service.check_in_work_order(
        session, tenant_id, wo.id, technician_id=technician.id,
        payload={"latitude": 28.6139, "longitude": 77.2090, "gps_accuracy_m": 12}, actor="test")
    workorder_service.start_work(session, tenant_id, wo.id, actor="test")
    checklist = dict(NEW_INSTALL_CHECKLIST)
    checklist["SCAN_SERIAL"] = serial
    checklist["RECORD_MAC"] = mac
    checklist_service.submit_responses(session, tenant_id, wo.id, responses=checklist,
                                       submitted_by="tech-1", correlation_id="c-1")
    proof_service.add_proof(session, tenant_id, wo.id, evidence_key=f"proof-photo-{suffix}", evidence_type="PHOTOGRAPH",
                            file_ref="file-proof-1", checksum="abc", capture_timestamp=TODAY, actor="test")
    proof_service.add_proof(session, tenant_id, wo.id, evidence_key=f"proof-serial-{suffix}", evidence_type="SERIAL_NUMBER",
                            file_ref="file-proof-1", checksum="abc", capture_timestamp=TODAY, actor="test")
    proof_service.add_proof(session, tenant_id, wo.id, evidence_key=f"proof-ack-{suffix}", evidence_type="CUSTOMER_ACKNOWLEDGEMENT",
                            file_ref="file-proof-ack", checksum="abc", capture_timestamp=TODAY, actor="test")
    proof_service.record_customer_acknowledgement(session, tenant_id, wo.id, method="CUSTOMER_SIGNATURE",
                                                  masked_recipient="cus***01", result="CONFIRMED", actor="test")
    from app.services import inventory_service

    inventory_service.record_material_usage(session, tenant_id, wo.id, material_code="FIBER_CONNECTOR",
                                            quantity=1, actor="test")
    inventory_service.record_material_usage(session, tenant_id, wo.id, material_code="SPLICE",
                                            quantity=1, actor="test")
    inventory_service.record_device_installation(session, tenant_id, wo.id, device_type="ONT",
                                                 serial_number=serial, mac_address=mac,
                                                 actor="test")
    wo = workorder_service.finish_execution(session, tenant_id, wo.id, actor="test")
    return wo


def test_create_generates_number_and_events(session, tenant_id, make_work_order):
    wo = make_work_order()
    assert wo.work_order_number.startswith("WO-")
    assert wo.status == "CREATED"
    assert wo.dispatch_state == "UNASSIGNED"
    events = workorder_service.__dict__  # noqa: F841
    from app.services.audit_service import work_order_events

    events = list(work_order_events(session, wo.id))
    assert any(e.event_type == "work_order.created" for e in events)
    assert wo.template_snapshot.get("required_skills") == ["FIBER_INSTALL", "ONT_INSTALL"]


def test_create_rejects_unknown_type(session, tenant_id, make_work_order):
    with pytest.raises(ValidationError):
        make_work_order(work_order_type="BOGUS_TYPE")


def test_idempotent_create_by_key(session, tenant_id, defaults):
    payload = dict(work_order_type="FAULT_REPAIR", customer_id="CUST-0001", source_channel="API")
    wo1 = workorder_service.create_work_order(session, tenant_id, **payload, idempotency_key="idem-1", actor="test")
    session.commit()
    wo2 = workorder_service.create_work_order(session, tenant_id, **payload, idempotency_key="idem-1", actor="test")
    session.commit()
    assert wo1.id == wo2.id


def test_validate_reaches_ready_for_scheduling(session, tenant_id, make_work_order):
    wo = make_work_order()
    wo = workorder_service.validate_work_order(session, tenant_id, wo.id, actor="test")
    assert wo.status == "READY_FOR_SCHEDULING"


def test_schedule_and_assign(session, tenant_id, make_work_order, make_technician):
    wo = make_work_order()
    technician = make_technician("Tech A", skills=["FIBER_INSTALL", "ONT_INSTALL"],
                                 certifications=[{"certification": "FIBER_SAFETY"}])
    appointment, wo = _schedule_and_assign(session, tenant_id, wo, technician)
    assert wo.status == "ASSIGNED"
    assert str(wo.assigned_technician_id) == str(technician.id)
    assignment = session.scalars(select(WorkOrderAssignment).where(
        WorkOrderAssignment.work_order_id == wo.id)).first()
    assert assignment is not None
    assert assignment.score > 0
    assert "skills" in assignment.score_breakdown


def test_manual_assign_requires_reason(session, tenant_id, make_work_order, make_technician):
    wo = make_work_order()
    technician = make_technician("Tech B", skills=["FIBER_INSTALL", "ONT_INSTALL"])
    workorder_service.validate_work_order(session, tenant_id, wo.id, actor="test")
    from app.services import appointment_service

    appointment_service.schedule(session, tenant_id, wo, window_start=_add_days(1),
                                 window_end=_add_days(1) + timedelta(hours=2), actor="test")
    with pytest.raises(ValidationError):
        workorder_service.assign_work_order(session, tenant_id, wo.id, technician_id=technician.id,
                                            actor="test")


def test_reassign_requires_reason_and_moves_technician(session, tenant_id, make_work_order, make_technician):
    wo = make_work_order()
    tech1 = make_technician("Tech One", skills=["FIBER_INSTALL", "ONT_INSTALL"])
    tech2 = make_technician("Tech Two", skills=["FIBER_INSTALL", "ONT_INSTALL"])
    _, wo = _schedule_and_assign(session, tenant_id, wo, tech1)
    wo = workorder_service.assign_work_order(session, tenant_id, wo.id, technician_id=tech2.id,
                                             reason="reassigning for coverage", actor="test")
    assert str(wo.assigned_technician_id) == str(tech2.id)
    assert wo.assigned_technician_name == "Tech Two"


def test_full_execution_and_qa_completion(session, tenant_id, make_work_order, make_technician):
    wo = make_work_order()
    technician = make_technician("Tech Full", skills=["FIBER_INSTALL", "ONT_INSTALL"],
                                 certifications=[{"certification": "FIBER_SAFETY"}])
    _, wo = _schedule_and_assign(session, tenant_id, wo, technician)
    workorder_service.dispatch_work_order(session, tenant_id, wo.id, actor="test")
    assert wo.status == "DISPATCHED"

    wo = _complete_execution(session, tenant_id, wo, technician)
    assert wo.status == "EXECUTION_COMPLETED"

    wo = workorder_service.submit_for_verification(session, tenant_id, wo.id, actor="test")
    assert wo.status == "VERIFICATION_PENDING"
    review = qa_service.get_review(session, wo)
    assert review is not None and review.state == "PENDING"

    review = qa_service.approve_review(session, tenant_id, wo.id, reviewer="qa-user")
    session.commit()
    assert review.state == "APPROVED"
    assert wo.status == "COMPLETED"
    assert wo.result_code is not None


def test_qa_reject_returns_to_rework(session, tenant_id, make_work_order, make_technician):
    wo = make_work_order()
    technician = make_technician("Tech QA", skills=["FIBER_INSTALL", "ONT_INSTALL"],
                                 certifications=[{"certification": "FIBER_SAFETY"}])
    _, wo = _schedule_and_assign(session, tenant_id, wo, technician)
    workorder_service.dispatch_work_order(session, tenant_id, wo.id, actor="test")
    wo = _complete_execution(session, tenant_id, wo, technician)
    workorder_service.submit_for_verification(session, tenant_id, wo.id, actor="test")
    review = qa_service.reject_review(session, tenant_id, wo.id, reviewer="qa-user", reason="photo unclear")
    session.commit()
    assert review.state == "REWORK_REQUIRED"
    assert wo.status == "QA_REJECTED"

    # Technician can resume execution on rework.
    wo = workorder_service.start_work(session, tenant_id, wo.id, actor="test")
    assert wo.status == "IN_PROGRESS"


def test_finish_requires_checklist_and_proof(session, tenant_id, make_work_order, make_technician):
    wo = make_work_order()
    technician = make_technician("Tech Strict", skills=["FIBER_INSTALL", "ONT_INSTALL"],
                                 certifications=[{"certification": "FIBER_SAFETY"}])
    _, wo = _schedule_and_assign(session, tenant_id, wo, technician)
    workorder_service.dispatch_work_order(session, tenant_id, wo.id, actor="test")
    workorder_service.start_travel(session, tenant_id, wo.id, actor="test")
    workorder_service.check_in_work_order(
        session, tenant_id, wo.id, technician_id=technician.id,
        payload={"latitude": 28.6139, "longitude": 77.2090, "gps_accuracy_m": 12}, actor="test")
    workorder_service.start_work(session, tenant_id, wo.id, actor="test")
    # No checklist / proof / material recorded -> finish must fail.
    with pytest.raises((ChecklistError, ProofError)):
        workorder_service.finish_execution(session, tenant_id, wo.id, actor="test")


def test_blocker_and_resume(session, tenant_id, make_work_order, make_technician):
    wo = make_work_order()
    technician = make_technician("Tech Blocked", skills=["FIBER_INSTALL", "ONT_INSTALL"],
                                 certifications=[{"certification": "FIBER_SAFETY"}])
    _, wo = _schedule_and_assign(session, tenant_id, wo, technician)
    workorder_service.dispatch_work_order(session, tenant_id, wo.id, actor="test")
    workorder_service.start_work(session, tenant_id, wo.id, actor="test")
    wo = workorder_service.record_blocker(session, tenant_id, wo.id, blocker_type="ACCESS_DENIED",
                                         reason="gate locked", actor="test")
    assert wo.status == "BLOCKED"
    wo = workorder_service.resume_work_order(session, tenant_id, wo.id, actor="test")
    assert wo.status == "IN_PROGRESS"


def test_pause_resume_sla_timer(session, tenant_id, make_work_order, make_technician):
    """Policy-listed pause states (e.g. AWAITING_PARTS) pause the field SLA;
    resuming restarts the timer."""
    wo = make_work_order()
    technician = make_technician("Tech Pause", skills=["FIBER_INSTALL", "ONT_INSTALL"],
                                 certifications=[{"certification": "FIBER_SAFETY"}])
    _, wo = _schedule_and_assign(session, tenant_id, wo, technician)
    workorder_service.dispatch_work_order(session, tenant_id, wo.id, actor="test")
    workorder_service.start_work(session, tenant_id, wo.id, actor="test")
    assert wo.field_sla_status == "ACTIVE"
    wo = workorder_service.request_parts(session, tenant_id, wo.id,
                                         materials=[{"material_code": "SPLICE", "quantity": 1}], actor="test")
    assert wo.status == "AWAITING_PARTS"
    assert wo.field_sla_status == "PAUSED"
    wo = workorder_service.resume_work_order(session, tenant_id, wo.id, actor="test")
    assert wo.status == "IN_PROGRESS"
    assert wo.field_sla_status == "ACTIVE"


def test_cancel(session, tenant_id, make_work_order):
    wo = make_work_order()
    wo = workorder_service.cancel_work_order(session, tenant_id, wo.id, reason="customer cancelled", actor="test")
    assert wo.status == "CANCELLED"
    with pytest.raises(ValidationError):
        workorder_service.cancel_work_order(session, tenant_id, wo.id, reason="again", actor="test")


def test_fail(session, tenant_id, make_work_order):
    wo = make_work_order()
    wo = workorder_service.fail_work_order(session, tenant_id, wo.id, reason="site inaccessible", actor="test")
    assert wo.status == "FAILED"


def test_illegal_direct_transition_blocked(session, tenant_id, make_work_order):
    wo = make_work_order()
    # CREATED -> COMPLETED is illegal via the state machine.
    from app.state_machine import work_order_transition

    with pytest.raises(ValueError):
        work_order_transition("CREATED", "COMPLETED")


def test_request_parts_and_remote_action(session, tenant_id, make_work_order, make_technician):
    wo = make_work_order()
    technician = make_technician("Tech Parts", skills=["FIBER_INSTALL", "ONT_INSTALL"],
                                 certifications=[{"certification": "FIBER_SAFETY"}])
    _, wo = _schedule_and_assign(session, tenant_id, wo, technician)
    workorder_service.dispatch_work_order(session, tenant_id, wo.id, actor="test")
    workorder_service.start_work(session, tenant_id, wo.id, actor="test")
    wo = workorder_service.request_parts(session, tenant_id, wo.id,
                                         materials=[{"material_code": "SPLICE", "quantity": 2}], actor="test")
    assert wo.status == "AWAITING_PARTS"
    wo = workorder_service.resume_work_order(session, tenant_id, wo.id, actor="test")
    wo = workorder_service.request_remote_action(session, tenant_id, wo.id, actor="test")
    assert wo.status == "AWAITING_REMOTE_ACTION"


def test_complete_blocked_before_qa(session, tenant_id, make_work_order, make_technician):
    wo = make_work_order()
    technician = make_technician("Tech NoQA", skills=["FIBER_INSTALL", "ONT_INSTALL"],
                                 certifications=[{"certification": "FIBER_SAFETY"}])
    _, wo = _schedule_and_assign(session, tenant_id, wo, technician)
    # QA is required for NEW_INSTALLATION; completing before review is blocked.
    with pytest.raises((ValidationError, QAError)):
        workorder_service.complete_work_order(session, tenant_id, wo.id, result_code="COMPLETED",
                                              summary="done", actor="test")
