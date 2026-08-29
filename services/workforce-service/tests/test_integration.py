"""Cross-service integration: OSS orders and support tickets create canonical
work orders; activation events advance remote-action work orders; customer
updates refresh names. All consumers are idempotent."""
import uuid

from app.messaging.consumers import handle_event
from app.models import WorkOrder
from app.services import workorder_service


def _event(event_type, tenant_id, payload, event_id=None):
    return {"id": event_id or str(uuid.uuid4()), "event_type": event_type,
            "tenant_id": str(tenant_id), "payload": payload}


def test_oss_order_creates_new_installation(session, tenant_id, defaults):
    event = _event("oss.order.field_work_required.v1", tenant_id, {
        "order_id": "ORD-100", "order_number": "ORD-100", "order_type": "NEW_CONNECTION",
        "customer_id": "CUST-0001", "service_subscription_id": "SUB-0001",
        "service_location_id": "loc-1", "latitude": 28.6139, "longitude": 77.2090,
    })
    result = handle_event(session, event)
    assert result["handled"] is True
    wo = session.scalars(__import__("sqlalchemy").select(WorkOrder).where(
        WorkOrder.oss_order_id == "ORD-100")).first()
    assert wo is not None
    assert wo.work_order_type == "NEW_INSTALLATION"
    assert wo.source_channel == "OSS"
    assert wo.customer_id == "CUST-0001"


def test_oss_duplicate_event_is_idempotent(session, tenant_id, defaults):
    event = _event("oss.order.field_work_required.v1", tenant_id, {
        "order_id": "ORD-101", "order_number": "ORD-101", "order_type": "NEW_CONNECTION",
        "customer_id": "CUST-0001", "service_subscription_id": "SUB-0001",
        "service_location_id": "loc-1",
    }, event_id="oss-ev-dup")
    first = handle_event(session, event)
    second = handle_event(session, event)
    assert first["handled"] is True
    assert second["handled"] is False and second["action"] == "duplicate"
    count = len(session.query(WorkOrder).filter_by(oss_order_id="ORD-101").all())
    assert count == 1


def test_support_ticket_creates_fault_repair(session, tenant_id, defaults):
    event = _event("support.ticket.field_visit_requested.v1", tenant_id, {
        "ticket_id": "TKT-1", "ticket_number": "TKT-2026-00000001",
        "customer_id": "CUST-0001", "service_subscription_id": "SUB-0001",
        "service_location_id": "loc-1",
    })
    result = handle_event(session, event)
    assert result["handled"] is True
    wo = session.query(WorkOrder).filter_by(support_ticket_id="TKT-1").first()
    assert wo is not None
    assert wo.work_order_type == "FAULT_REPAIR"
    assert wo.source_channel == "SUPPORT"


def test_nms_repair_creates_high_priority(session, tenant_id, defaults):
    event = _event("nms.repair_required.v1", tenant_id, {
        "incident_id": "INC-9", "customer_id": "CUST-0001",
        "service_location_id": "loc-1", "priority": "P1_CRITICAL",
    })
    result = handle_event(session, event)
    assert result["handled"] is True
    wo = session.query(WorkOrder).filter_by(nms_incident_id="INC-9").first()
    assert wo is not None
    assert wo.priority == "P1_CRITICAL"


def test_activation_completed_advances_remote_action(session, tenant_id, defaults, make_work_order, make_technician):
    from datetime import datetime, timedelta, timezone

    today = datetime.now(timezone.utc).replace(hour=10, minute=0, second=0, microsecond=0)
    wo = make_work_order(oss_order_id="ORD-200", oss_order_number="ORD-200")
    technician = make_technician("Act Tech", skills=["FIBER_INSTALL", "ONT_INSTALL"],
                                 certifications=[{"certification": "FIBER_SAFETY"}])
    from app.services import appointment_service

    workorder_service.validate_work_order(session, tenant_id, wo.id, actor="test")
    appointment_service.schedule(session, tenant_id, wo, window_start=today + timedelta(days=1),
                                 window_end=today + timedelta(days=1, hours=2), actor="test")
    workorder_service.assign_work_order(session, tenant_id, wo.id, technician_id=technician.id,
                                        reason="activation test", actor="test")
    workorder_service.dispatch_work_order(session, tenant_id, wo.id, actor="test")
    workorder_service.start_work(session, tenant_id, wo.id, actor="test")
    wo = workorder_service.request_remote_action(session, tenant_id, wo.id, actor="test")
    assert wo.status == "AWAITING_REMOTE_ACTION"
    session.commit()

    event = _event("oss.service.activation_completed.v1", tenant_id, {"order_id": "ORD-200"})
    result = handle_event(session, event)
    assert result["handled"] is True
    session.refresh(wo)
    assert wo.status == "IN_PROGRESS"


def test_customer_updated_refreshes_name(session, tenant_id, defaults, make_work_order):
    wo = make_work_order(customer_id="CUST-UPD", customer_name="Old Name")
    event = _event("crm.customer.updated.v1", tenant_id, {"customer_id": "CUST-UPD", "name": "New Name"})
    result = handle_event(session, event)
    assert result["handled"] is True
    session.refresh(wo)
    assert wo.customer_name == "New Name"


def test_unknown_event_ignored(session, tenant_id, defaults):
    event = _event("billing.invoice.generated.v1", tenant_id, {"invoice_id": "INV-1"})
    result = handle_event(session, event)
    assert result["handled"] is True
    assert result["action"] == "ignored"
