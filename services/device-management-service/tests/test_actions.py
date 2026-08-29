"""Governed device actions: approval workflow, elevated permissions, SSRF
protection on connection requests, and execution."""
import pytest

from app.domain.exceptions import AuthorizationRequiredError, SSRFProtectionError, ValidationError
from app.models import DeviceAction
from app.services import action_service


@pytest.fixture
def cpe(session, tenant_id, acs, make_acs_device, make_device):
    device, acs_device_id = make_device(serial="SN-ACT", product_class="AN5506")
    return {"device": device, "acs_device_id": acs_device_id, "client": acs["client"]}


def test_reboot_action_requires_no_approval(session, tenant_id, cpe):
    action = action_service.create_action(session, tenant_id, cpe["device"].id, action_type="REBOOT",
                                          requested_by="tech", actor="test")
    assert action.requires_approval is False
    assert action.state == "REQUESTED"


def test_factory_reset_requires_approval(session, tenant_id, cpe):
    action = action_service.create_action(session, tenant_id, cpe["device"].id, action_type="FACTORY_RESET",
                                          requested_by="tech", actor="test")
    assert action.requires_approval is True
    assert action.state == "AUTHORIZATION_REQUIRED"
    with pytest.raises(AuthorizationRequiredError):
        action_service.execute_action(session, tenant_id, action.id, actor="test")


def test_approve_and_execute_reboot(session, tenant_id, cpe):
    action = action_service.create_action(session, tenant_id, cpe["device"].id, action_type="REBOOT",
                                          requested_by="tech", actor="test")
    action = action_service.execute_action(session, tenant_id, action.id, actor="test")
    assert action.state == "EXECUTING"
    assert action.genieacs_task_id is not None
    action = action_service.complete_action(session, tenant_id, action.id, ok=True, result={}, actor="test")
    assert action.state == "SUCCEEDED"
    # Reboot event published.
    from app.models import OutboxEvent

    assert session.query(OutboxEvent).filter_by(event_type="cpe.rebooted.v1").count() == 1


def test_action_failure(session, tenant_id, cpe):
    action = action_service.create_action(session, tenant_id, cpe["device"].id, action_type="REBOOT",
                                          requested_by="tech", actor="test")
    action_service.execute_action(session, tenant_id, action.id, actor="test")
    action = action_service.complete_action(session, tenant_id, action.id, ok=False,
                                            result={"code": "CWMP_FAULT", "detail": "reboot rejected"}, actor="test")
    assert action.state == "FAILED"
    assert action.failure_code == "CWMP_FAULT"


def test_connection_request_validates_url(session, tenant_id, cpe):
    # A private/metadata URL must be rejected at action creation.
    with pytest.raises(SSRFProtectionError):
        action_service.create_action(session, tenant_id, cpe["device"].id, action_type="CONNECTION_REQUEST",
                                     parameters={"connection_request_url": "http://169.254.169.254/latest/"}, actor="test")
    # A valid public URL is accepted.
    action = action_service.create_action(session, tenant_id, cpe["device"].id, action_type="CONNECTION_REQUEST",
                                          parameters={"connection_request_url": "http://cpe.example.com:7547/"},
                                          actor="test")
    assert action.action_type == "CONNECTION_REQUEST"


def test_unknown_action_type_rejected(session, tenant_id, cpe):
    with pytest.raises(ValidationError):
        action_service.create_action(session, tenant_id, cpe["device"].id, action_type="MALFORM", actor="test")


def test_idempotent_action_create(session, tenant_id, cpe):
    a1 = action_service.create_action(session, tenant_id, cpe["device"].id, action_type="REBOOT",
                                      requested_by="tech", actor="test", idempotency_key="act-idem-1")
    a2 = action_service.create_action(session, tenant_id, cpe["device"].id, action_type="REBOOT",
                                      requested_by="tech", actor="test", idempotency_key="act-idem-1")
    assert a1.id == a2.id
