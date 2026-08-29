"""Cross-service event consumers: OSS provisioning intent, inventory install,
support diagnostics, service changes, NMS investigation — all idempotent."""
import uuid

from app.messaging.consumers import handle_event
from app.models import ConfigurationJob, DiagnosticJob, ManagedCpe
from app.services import profile_service
from conftest import variant_for


def _event(event_type, tenant_id, payload, event_id=None):
    return {"id": event_id or str(uuid.uuid4()), "event_type": event_type,
            "tenant_id": str(tenant_id), "payload": payload}


def test_order_provisioning_queues_job(session, tenant_id, defaults, make_acs_device, make_device, make_profile):
    profile, version = make_profile(code="PROV_PROFILE")
    device, acs_device_id = make_device(serial="SN-PROV", product_class="AN5506")
    variant = variant_for(session, model_name="AN5506-04-F1")
    device.model_variant_id = variant.id
    session.commit()
    profile_service.add_assignment_rule(session, tenant_id, profile.id, facts={}, priority=1, actor="test")
    session.commit()

    result = handle_event(session, _event("order.cpe_provisioning_requested.v1", tenant_id, {
        "order_id": "ORD-9", "serial_number": "SN-PROV", "customer_id": "CUST-1",
        "service_subscription_id": "SUB-1", "service_location_id": "LOC-1",
    }))
    assert result["handled"] is True
    assert "provisioning:" in result["action"]
    session.refresh(device)
    assert device.oss_order_id == "ORD-9"
    job = session.query(ConfigurationJob).filter_by(cpe_id=device.id).first()
    assert job is not None and job.state == "QUEUED"


def test_order_provisioning_device_not_found(session, tenant_id, defaults):
    result = handle_event(session, _event("order.cpe_provisioning_requested.v1", tenant_id, {
        "order_id": "ORD-10", "serial_number": "SN-NOPE", "customer_id": "CUST-1"}))
    assert result["handled"] is True
    assert result["action"] == "provisioning:device_not_found"


def test_inventory_installed_links_device(session, tenant_id, defaults, make_device):
    device, _ = make_device(serial="SN-INST", product_class="AN5506")
    session.commit()
    result = handle_event(session, _event("inventory.device_installed.v1", tenant_id, {
        "serial_number": "SN-INST", "asset_id": "INV-1", "work_order_id": "WO-1", "order_id": "ORD-1"}))
    assert result["handled"] is True
    session.refresh(device)
    assert device.inventory_asset_id == "INV-1"
    assert device.work_order_id == "WO-1"


def test_ticket_diagnostic_requested(session, tenant_id, defaults, make_device):
    device, _ = make_device(serial="SN-TKT", product_class="AN5506")
    session.commit()
    result = handle_event(session, _event("ticket.device_diagnostic_requested.v1", tenant_id, {
        "serial_number": "SN-TKT", "ticket_id": "TKT-77", "diagnostic_type": "PING"}))
    assert result["handled"] is True
    job = session.query(DiagnosticJob).filter_by(cpe_id=device.id).first()
    assert job is not None and job.diagnostic_type == "PING"
    assert job.support_ticket_id == "TKT-77"


def test_nms_investigation_requested(session, tenant_id, defaults, make_device):
    device, _ = make_device(serial="SN-NMS", product_class="AN5506")
    session.commit()
    result = handle_event(session, _event("nms.device_investigation_requested.v1", tenant_id, {
        "serial_number": "SN-NMS", "diagnostic_type": "WAN_STATUS"}))
    assert result["handled"] is True
    job = session.query(DiagnosticJob).filter_by(cpe_id=device.id).first()
    assert job is not None


def test_duplicate_event_idempotent(session, tenant_id, defaults, make_device):
    make_device(serial="SN-DUP", product_class="AN5506")
    event = _event("inventory.device_installed.v1", tenant_id,
                   {"serial_number": "SN-DUP", "asset_id": "INV-9"}, event_id="inv-inst-dup")
    first = handle_event(session, event)
    second = handle_event(session, event)
    assert first["handled"] is True
    assert second["handled"] is False and second["action"] == "duplicate"


def test_unknown_event_ignored(session, tenant_id, defaults):
    result = handle_event(session, _event("billing.invoice.generated.v1", tenant_id, {"invoice_id": "I-1"}))
    assert result["handled"] is True
    assert result["action"] == "ignored"
