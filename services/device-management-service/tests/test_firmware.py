"""Firmware: checksum validation, approval, compatibility, canary rollouts,
thresholds, pause/advance, post-upgrade verification and capability-aware
rollback claims."""
import hashlib

import pytest

from app.domain import firmware as fw_rules
from app.domain.exceptions import FirmwareError, RolloutError
from app.models import FirmwareDeployment, FirmwareVerification
from app.services import firmware_service
from conftest import variant_for


@pytest.fixture
def cpe(session, tenant_id, acs, make_acs_device, make_device):
    device, acs_device_id = make_device(serial="SN-FW", product_class="AN5506")
    variant = variant_for(session, model_name="AN5506-04-F1")
    device.model_variant_id = variant.id
    device.firmware_version = "V1.0"
    session.commit()
    return {"device": device, "acs_device_id": acs_device_id, "client": acs["client"]}


def _checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _upload(session, tenant_id, *, data=None, version="V2.0", **kw):
    data = data or b"\x00\x01\x02firmware"
    return firmware_service.upload_firmware(session, tenant_id, vendor="FiberHome", model="AN5506-04-F1",
                                            version=version, checksum_sha256=_checksum(data), data=data,
                                            uploaded_by="test", actor="test", **kw)


def test_checksum_validation(session, tenant_id, defaults):
    artifact = _upload(session, tenant_id)
    assert artifact.approval_state == "UPLOADED"

    with pytest.raises(FirmwareError):
        firmware_service.upload_firmware(session, tenant_id, vendor="FiberHome", model="AN5506-04-F1",
                                         version="V3.0", checksum_sha256="0" * 64, data=b"some-bytes",
                                         uploaded_by="test", actor="test")


def test_duplicate_upload_rejected(session, tenant_id, defaults):
    _upload(session, tenant_id)
    with pytest.raises(FirmwareError):
        _upload(session, tenant_id)


def test_approve_firmware(session, tenant_id, defaults):
    artifact = _upload(session, tenant_id)
    artifact = firmware_service.approve_firmware(session, tenant_id, artifact.id, decision="APPROVED",
                                                 reviewed_by="fw-approver", actor="test")
    assert artifact.approval_state == "APPROVED"
    assert artifact.approved_by == "fw-approver"


def test_rollout_requires_approved_artifact(session, tenant_id, defaults):
    artifact = _upload(session, tenant_id)
    with pytest.raises(RolloutError):
        firmware_service.create_rollout(session, tenant_id, artifact_id=artifact.id, name="r",
                                        strategy="CANARY", policy={"stage_size": 1}, actor="test")


def test_rollout_canary_stages(session, tenant_id, defaults, cpe):
    artifact = _upload(session, tenant_id)
    firmware_service.approve_firmware(session, tenant_id, artifact.id, decision="APPROVED",
                                      reviewed_by="fw", actor="test")
    rollout = firmware_service.create_rollout(session, tenant_id, artifact_id=artifact.id, name="canary",
                                              strategy="CANARY",
                                              policy={"stage_percentages": [1, 5, 10, 25, 59],
                                                      "observation_period_minutes": 30,
                                                      "success_threshold": 0.95, "failure_threshold": 0.1},
                                              actor="test")
    stages = firmware_service.build_rollout_stages(session, tenant_id, rollout.id, fleet_size=100, actor="test")
    assert len(stages) == 5
    assert stages[0].size == 1  # canary: smallest first
    assert stages[0].requires_manual_approval is True
    firmware_service.start_rollout(session, tenant_id, rollout.id, actor="test")
    assert rollout.state == "RUNNING"


def test_deployment_verify_success(session, tenant_id, defaults, cpe):
    artifact = _upload(session, tenant_id, version="V2.0")
    firmware_service.approve_firmware(session, tenant_id, artifact.id, decision="APPROVED",
                                      reviewed_by="fw", actor="test")
    rollout = firmware_service.create_rollout(session, tenant_id, artifact_id=artifact.id, name="r",
                                              strategy="LAB", policy={"stage_size": 1,
                                                                      "success_threshold": 0.95,
                                                                      "failure_threshold": 0.1}, actor="test")
    firmware_service.start_rollout(session, tenant_id, rollout.id, actor="test")
    deployment = firmware_service.queue_deployment(session, tenant_id, rollout_id=rollout.id,
                                                   cpe_id=cpe["device"].id, actor="test")
    deployment = firmware_service.execute_deployment(session, tenant_id, deployment.id, actor="test")
    # Device reports the new firmware after reboot.
    cpe["client"].set_device_parameters(cpe["acs_device_id"],
                                        {"Device.DeviceInfo.SoftwareVersion": "V2.0"})
    deployment = firmware_service.complete_deployment(session, tenant_id, deployment.id,
                                                      reported_firmware="V2.0", health_checks={"ping": True},
                                                      actor="test")
    assert deployment.state == "SUCCEEDED"
    verification = session.query(FirmwareVerification).filter_by(deployment_id=deployment.id).first()
    assert verification.verified is True


def test_deployment_verification_failure(session, tenant_id, defaults, cpe):
    artifact = _upload(session, tenant_id, version="V2.0")
    firmware_service.approve_firmware(session, tenant_id, artifact.id, decision="APPROVED",
                                      reviewed_by="fw", actor="test")
    rollout = firmware_service.create_rollout(session, tenant_id, artifact_id=artifact.id, name="r",
                                              strategy="LAB", policy={"stage_size": 1,
                                                                      "success_threshold": 0.95,
                                                                      "failure_threshold": 0.1}, actor="test")
    firmware_service.start_rollout(session, tenant_id, rollout.id, actor="test")
    deployment = firmware_service.queue_deployment(session, tenant_id, rollout_id=rollout.id,
                                                   cpe_id=cpe["device"].id, actor="test")
    firmware_service.execute_deployment(session, tenant_id, deployment.id, actor="test")
    # Device reports the OLD firmware (upgrade did not take).
    deployment = firmware_service.complete_deployment(session, tenant_id, deployment.id,
                                                      reported_firmware="V1.0", actor="test")
    assert deployment.state in ("FAILED", "ROLLED_BACK")


def test_rollback_claimed_only_when_supported(session, tenant_id, defaults, cpe):
    variant = variant_for(session, model_name="HN8255W")  # DUAL_BANK
    assert variant.rollback_capability == "DUAL_BANK"
    assert fw_rules.rollback_claim_supported(variant.rollback_capability) is True
    huawei_variant = variant_for(session, model_name="AN5506-04-F1")
    assert fw_rules.rollback_claim_supported(huawei_variant.rollback_capability) is False


def test_canary_threshold_pauses(session, tenant_id, defaults, cpe):
    artifact = _upload(session, tenant_id)
    firmware_service.approve_firmware(session, tenant_id, artifact.id, decision="APPROVED",
                                      reviewed_by="fw", actor="test")
    rollout = firmware_service.create_rollout(session, tenant_id, artifact_id=artifact.id, name="r",
                                              strategy="CANARY",
                                              policy={"stage_percentages": [1, 5],
                                                      "success_threshold": 0.95, "failure_threshold": 0.1}, actor="test")
    stages = firmware_service.build_rollout_stages(session, tenant_id, rollout.id, fleet_size=100, actor="test")
    firmware_service.start_rollout(session, tenant_id, rollout.id, actor="test")
    session.commit()
    # Two failures in the first stage exceed the 10% failure threshold -> pause.
    stage = stages[0]
    for i in range(2):
        deployment = firmware_service.queue_deployment(session, tenant_id, rollout_id=rollout.id,
                                                       cpe_id=cpe["device"].id, stage_id=stage.id, actor="test")
        firmware_service.execute_deployment(session, tenant_id, deployment.id, actor="test")
        firmware_service.complete_deployment(session, tenant_id, deployment.id,
                                             reported_firmware="V9.9", actor="test")
        session.commit()
    result = firmware_service.advance_rollout_stages(session, tenant_id, rollout.id, actor="test")
    assert result["paused"] is True
    session.refresh(rollout)
    assert rollout.state in ("PAUSED", "AUTO_PAUSED", "RUNNING")


def test_compute_stage_size_canary():
    assert fw_rules.compute_stage_size("CANARY", 100, 1, 1) == 1
    assert fw_rules.compute_stage_size("PERCENTAGE", 100, 2, 10) == 10
