"""End-to-end device lifecycle: inventory reserve -> install -> first Inform ->
claim -> profile -> configuration verified -> firmware canary upgrade."""
import hashlib
import uuid

import pytest

from app.integrations.fakes import STATE
from app.messaging.consumers import handle_event
from app.models import ConfigurationJob, ManagedCpe, OutboxEvent
from app.services import (
    configuration_service,
    device_service,
    firmware_service,
    profile_service,
)
from conftest import variant_for


def _checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_full_device_lifecycle(session, tenant_id, defaults, make_acs_device, make_profile):
    # 1. Inventory reserves an ONT and the technician installs it.
    STATE.seed_inventory_asset("E2E-SN1", model="ONT")
    handle_event(session, {
        "id": "e2e-inv-install", "event_type": "inventory.device_installed.v1",
        "tenant_id": str(tenant_id),
        "payload": {"serial_number": "E2E-SN1", "asset_id": "INV-E2E", "work_order_id": "WO-E2E"},
    })

    # 2. Device sends its first Inform -> discovered in ACS -> matched.
    acs_device_id = make_acs_device(serial_number="E2E-SN1", oui="A4B1C1", product_class="AN5506")
    from app.services import acs_service
    from app.models import ACSInstance

    instance = ACSInstance(tenant_id=None, name="e2e-acs", base_url="http://genieacs:7557", health="HEALTHY")
    session.add(instance)
    session.commit()
    session.refresh(instance)
    device = device_service.discover_from_acs(session, instance.id, acs_device_id=acs_device_id,
                                              requested_tenant_id=tenant_id, actor="e2e")
    assert device.state == "IDENTIFIED"

    # 3. Claimed with validated ownership (pre-registered serial evidence).
    device = device_service.claim_device(session, tenant_id, device.id, method="PREREGISTERED_SERIAL",
                                         evidence="E2E-SN1", actor="e2e")
    assert device.state == "CLAIMED"

    # 4. Assign business entities.
    device = device_service.assign_device(session, tenant_id, device.id, customer_id="CUST-E2E",
                                          service_subscription_id="SUB-E2E", service_location_id="LOC-E2E",
                                          oss_order_id="ORD-E2E", work_order_id="WO-E2E",
                                          inventory_serial="E2E-SN1", inventory_asset_id="INV-E2E", actor="e2e")
    variant = variant_for(session, model_name="AN5506-04-F1")
    device.model_variant_id = variant.id
    device.data_model_family = "TR181"
    session.commit()

    # 5. Profile selected via assignment rule.
    profile, version = make_profile(code="E2E_PROFILE")
    profile_service.add_assignment_rule(session, tenant_id, profile.id, facts={}, priority=1, actor="e2e")
    session.commit()
    selected, selected_version, decision = profile_service.resolve_profile_for_device(session, tenant_id, device)
    assert selected is not None and selected.id == profile.id

    # 6. Configuration job queued + executed + verified.
    job = configuration_service.create_configuration_job(session, tenant_id, device.id,
                                                          profile_version_id=version.id, requested_by="oss")
    configuration_service.approve_job(session, tenant_id, job.id, actor="oss")
    configuration_service.queue_job(session, tenant_id, job.id, actor="oss")
    configuration_service.execute_job(session, tenant_id, job.id, actor="oss")
    session.commit()
    session.refresh(job)
    assert job.state == "WAITING_FOR_INFORM"

    # 7. Device applies and reports (simulated), task completes, verification passes.
    from app.integrations.acs import get_acs_client

    acs_client = get_acs_client({"instance_id": str(instance.id)})
    compiled = dict(job.desired_parameters)
    acs_client.set_device_parameters(acs_device_id, compiled)
    task_id = _last_task(acs_device_id)
    job = configuration_service.process_task_result(session, tenant_id, job.id, task_id=task_id,
                                                    task_state="COMPLETED", actor="e2e")
    job = configuration_service.verify_job(session, tenant_id, job.id, actor="e2e")
    assert job.state == "SUCCEEDED"
    session.refresh(device)
    assert device.profile_compliance == "COMPLIANT"

    # 8. Firmware canary upgrade.
    data = b"\x00e2e-firmware"
    artifact = firmware_service.upload_firmware(session, tenant_id, vendor="FiberHome", model="AN5506-04-F1",
                                                version="V2.0", checksum_sha256=_checksum(data), data=data,
                                                uploaded_by="fw-op", actor="e2e")
    firmware_service.approve_firmware(session, tenant_id, artifact.id, decision="APPROVED",
                                      reviewed_by="fw-approver", actor="e2e")
    rollout = firmware_service.create_rollout(session, tenant_id, artifact_id=artifact.id, name="e2e-canary",
                                              strategy="CANARY",
                                              policy={"stage_percentages": [1, 5, 10, 25, 59],
                                                      "success_threshold": 0.95, "failure_threshold": 0.1},
                                              actor="e2e")
    stages = firmware_service.build_rollout_stages(session, tenant_id, rollout.id, fleet_size=100, actor="e2e")
    firmware_service.start_rollout(session, tenant_id, rollout.id, actor="e2e")
    deployment = firmware_service.queue_deployment(session, tenant_id, rollout_id=rollout.id, cpe_id=device.id,
                                                   stage_id=stages[0].id, actor="e2e")
    firmware_service.execute_deployment(session, tenant_id, deployment.id, actor="e2e")
    acs_client.set_device_parameters(acs_device_id, {"Device.DeviceInfo.SoftwareVersion": "V2.0"})
    deployment = firmware_service.complete_deployment(session, tenant_id, deployment.id,
                                                      reported_firmware="V2.0", health_checks={"ping": True},
                                                      actor="e2e")
    assert deployment.state == "SUCCEEDED"
    session.refresh(device)
    assert device.firmware_version == "V2.0"

    # 9. Key events published through the outbox.
    session.commit()
    published = [o.event_type for o in session.query(OutboxEvent).filter_by(tenant_id=tenant_id)]
    assert "cpe.discovered.v1" in published
    assert "cpe.claimed.v1" in published
    assert "cpe.assigned.v1" in published
    assert "cpe.configuration_requested.v1" in published
    assert "cpe.configuration_applied.v1" in published
    assert "cpe.firmware_upgrade_started.v1" in published
    assert "cpe.firmware_upgrade_completed.v1" in published


def _last_task(acs_device_id: str) -> str:
    from app.integrations.acs import FakeACSClient

    tasks = FakeACSClient._state["devices"][acs_device_id]["tasks"]
    return tasks[-1]


def test_unknown_device_quarantined(session, acs, make_acs_device, defaults):
    acs_device_id = make_acs_device(serial_number="E2E-UNKNOWN", oui="AA0001", product_class="MYSTERY")
    device = device_service.discover_from_acs(session, acs["instance"].id, acs_device_id=acs_device_id, actor="e2e")
    assert device.state == "QUARANTINED"
    assert device.tenant_id is None


def test_duplicate_rabbitmq_delivery_no_duplicate_jobs(session, tenant_id, defaults, make_device, make_profile):
    profile, version = make_profile(code="E2E_DUP")
    profile_service.add_assignment_rule(session, tenant_id, profile.id, facts={}, priority=1, actor="test")
    session.commit()
    device, _ = make_device(serial="E2E-DUP", product_class="AN5506")
    device.model_variant_id = variant_for(session, model_name="AN5506-04-F1").id
    session.commit()
    event = {
        "id": "e2e-order-dup", "event_type": "order.cpe_provisioning_requested.v1",
        "tenant_id": str(tenant_id),
        "payload": {"order_id": "ORD-DUP", "serial_number": "E2E-DUP", "customer_id": "CUST-1"},
    }
    first = handle_event(session, event)
    second = handle_event(session, event)
    assert first["handled"] is True
    assert second["handled"] is False
    assert session.query(ConfigurationJob).filter_by(cpe_id=device.id).count() == 1
