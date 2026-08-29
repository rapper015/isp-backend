"""Configuration jobs: apply + read-back verification, queued-vs-applied
semantics, offline/delayed-inform, idempotency, verification failure, timeout
and drift."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.domain.exceptions import ConfigurationError, ValidationError
from app.models import ConfigurationJob, ConfigurationVerification, DeviceObservedState
from app.services import configuration_service, device_service
from conftest import variant_for


@pytest.fixture
def cpe(session, tenant_id, acs, make_acs_device, make_device):
    """A claimed device with a fake-ACS binding."""
    device, acs_device_id = make_device(serial="SN-CFG", product_class="AN5506")
    variant = variant_for(session, model_name="AN5506-04-F1")
    device.model_variant_id = variant.id
    device.data_model_family = "TR181"
    session.commit()
    return {"device": device, "acs_device_id": acs_device_id, "client": acs["client"]}


def _params():
    return {"Device.WiFi.SSID.1.SSID": "TestNet", "Device.WiFi.Radio.1.Channel": 6}


def test_create_job_drafts_with_diff(session, tenant_id, cpe):
    job = configuration_service.create_configuration_job(session, tenant_id, cpe["device"].id,
                                                         parameters=_params(), requested_by="test")
    assert job.state == "DRAFT"
    assert job.desired_parameters["Device.WiFi.SSID.1.SSID"] == "TestNet"
    assert "diff_preview" in job.__dict__


def test_queued_task_is_not_success(session, tenant_id, cpe):
    job = configuration_service.create_configuration_job(session, tenant_id, cpe["device"].id,
                                                          parameters=_params(), requested_by="test")
    configuration_service.approve_job(session, tenant_id, job.id, actor="test")
    configuration_service.queue_job(session, tenant_id, job.id, actor="test")
    configuration_service.execute_job(session, tenant_id, job.id, actor="test")
    session.commit()
    session.refresh(job)
    # Dispatch alone never marks success — it waits for the device session.
    assert job.state in ("CONNECTION_REQUEST_PENDING", "WAITING_FOR_INFORM", "EXECUTING")
    assert job.state != "SUCCEEDED"


def test_successful_apply_and_verify(session, tenant_id, cpe):
    job = configuration_service.create_configuration_job(session, tenant_id, cpe["device"].id,
                                                          parameters=_params(), requested_by="test")
    configuration_service.approve_job(session, tenant_id, job.id, actor="test")
    configuration_service.queue_job(session, tenant_id, job.id, actor="test")
    configuration_service.execute_job(session, tenant_id, job.id, actor="test")
    # Device applies the parameters (simulated in the fake ACS) and completes the task.
    cpe["client"].set_device_parameters(cpe["acs_device_id"], _params())
    job = configuration_service.process_task_result(session, tenant_id, job.id,
                                                    task_id=_last_task(cpe["client"], cpe["acs_device_id"]),
                                                    task_state="COMPLETED", actor="test")
    assert job.state == "DEVICE_ACKNOWLEDGED"
    job = configuration_service.verify_job(session, tenant_id, job.id, actor="test")
    assert job.state == "SUCCEEDED"
    verification = session.query(ConfigurationVerification).filter_by(job_id=job.id).first()
    assert verification.state == "VERIFIED"


def _last_task(client, device_id):
    from app.integrations.acs import FakeACSClient

    tasks = FakeACSClient._state["devices"][device_id]["tasks"]
    return tasks[-1]


def test_verification_failure_when_device_did_not_apply(session, tenant_id, cpe):
    job = configuration_service.create_configuration_job(session, tenant_id, cpe["device"].id,
                                                          parameters=_params(), requested_by="test")
    configuration_service.approve_job(session, tenant_id, job.id, actor="test")
    configuration_service.queue_job(session, tenant_id, job.id, actor="test")
    configuration_service.execute_job(session, tenant_id, job.id, actor="test")
    # Device did NOT apply (no parameters set) but task 'completed'.
    job = configuration_service.process_task_result(session, tenant_id, job.id,
                                                    task_id=_last_task(cpe["client"], cpe["acs_device_id"]),
                                                    task_state="COMPLETED", actor="test")
    job = configuration_service.verify_job(session, tenant_id, job.id, actor="test")
    assert job.state == "FAILED"
    assert job.failure_code == "VERIFICATION_FAILED"


def test_offline_device_waits_for_inform(session, tenant_id, cpe):
    job = configuration_service.create_configuration_job(session, tenant_id, cpe["device"].id,
                                                          parameters=_params(), requested_by="test")
    configuration_service.approve_job(session, tenant_id, job.id, actor="test")
    configuration_service.queue_job(session, tenant_id, job.id, actor="test")
    cpe["client"].set_connection_request_outcome("UNREACHABLE")
    job = configuration_service.execute_job(session, tenant_id, job.id, actor="test")
    session.refresh(job)
    # Offline device: task stays queued for the next Inform; job waits, not failed.
    assert job.state == "WAITING_FOR_INFORM"
    # Later, the device comes online and completes the task on its periodic Inform.
    cpe["client"].set_device_parameters(cpe["acs_device_id"], _params())
    job = configuration_service.process_task_result(session, tenant_id, job.id,
                                                    task_id=_last_task(cpe["client"], cpe["acs_device_id"]),
                                                    task_state="COMPLETED", actor="test")
    job = configuration_service.verify_job(session, tenant_id, job.id, actor="test")
    assert job.state == "SUCCEEDED"


def test_task_fault_fails_job(session, tenant_id, cpe):
    job = configuration_service.create_configuration_job(session, tenant_id, cpe["device"].id,
                                                          parameters=_params(), requested_by="test")
    configuration_service.approve_job(session, tenant_id, job.id, actor="test")
    configuration_service.queue_job(session, tenant_id, job.id, actor="test")
    configuration_service.execute_job(session, tenant_id, job.id, actor="test")
    job = configuration_service.process_task_result(session, tenant_id, job.id,
                                                    task_id=_last_task(cpe["client"], cpe["acs_device_id"]),
                                                    task_state="FAULTED", task_result={"fault": "9003"}, actor="test")
    assert job.state == "FAILED"
    assert job.failure_code == "FAULTED"


def test_idempotent_create_by_key(session, tenant_id, cpe):
    job1 = configuration_service.create_configuration_job(session, tenant_id, cpe["device"].id,
                                                          parameters=_params(), requested_by="test",
                                                          idempotency_key="cfg-idem-1")
    job2 = configuration_service.create_configuration_job(session, tenant_id, cpe["device"].id,
                                                          parameters=_params(), requested_by="test",
                                                          idempotency_key="cfg-idem-1")
    assert job1.id == job2.id


def test_sensitive_parameter_not_drift(session, tenant_id, cpe):
    """Write-only secrets cannot be read back; they must not fail verification."""
    params = {"Device.WiFi.SSID.1.SSID": "TestNet",
              "Device.WiFi.AccessPoint.1.Security.KeyPassphrase": "topsecret"}
    job = configuration_service.create_configuration_job(session, tenant_id, cpe["device"].id,
                                                          parameters=params, requested_by="test")
    # Mark the passphrase code as sensitive via the catalogue definition mapping.
    from app.services import catalog_service

    sensitive = catalog_service.sensitive_definitions(session)
    assert sensitive  # catalogue has sensitive definitions
    configuration_service.approve_job(session, tenant_id, job.id, actor="test")
    configuration_service.queue_job(session, tenant_id, job.id, actor="test")
    configuration_service.execute_job(session, tenant_id, job.id, actor="test")
    cpe["client"].set_device_parameters(cpe["acs_device_id"], {"Device.WiFi.SSID.1.SSID": "TestNet"})
    job = configuration_service.process_task_result(session, tenant_id, job.id,
                                                    task_id=_last_task(cpe["client"], cpe["acs_device_id"]),
                                                    task_state="COMPLETED", actor="test")
    job = configuration_service.verify_job(session, tenant_id, job.id, actor="test")
    # SSID matches; the secret is unreadable but that is expected, not drift.
    assert job.state == "SUCCEEDED"


def test_timeout_stale_job(session, tenant_id, cpe):
    job = configuration_service.create_configuration_job(session, tenant_id, cpe["device"].id,
                                                          parameters=_params(), requested_by="test")
    configuration_service.approve_job(session, tenant_id, job.id, actor="test")
    configuration_service.queue_job(session, tenant_id, job.id, actor="test")
    job.timeout_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    session.commit()
    timed_out = configuration_service.timeout_stale_jobs(session, tenant_id)
    assert str(job.id) in timed_out
    session.refresh(job)
    assert job.state == "TIMED_OUT"


def test_drift_detection(session, tenant_id, cpe):
    # Establish desired state via a successful job.
    job = configuration_service.create_configuration_job(session, tenant_id, cpe["device"].id,
                                                          parameters=_params(), requested_by="test")
    configuration_service.approve_job(session, tenant_id, job.id, actor="test")
    configuration_service.queue_job(session, tenant_id, job.id, actor="test")
    configuration_service.execute_job(session, tenant_id, job.id, actor="test")
    cpe["client"].set_device_parameters(cpe["acs_device_id"], _params())
    job = configuration_service.process_task_result(session, tenant_id, job.id,
                                                    task_id=_last_task(cpe["client"], cpe["acs_device_id"]),
                                                    task_state="COMPLETED", actor="test")
    configuration_service.verify_job(session, tenant_id, job.id, actor="test")
    session.commit()
    # Customer changed the SSID on the device.
    cpe["client"].set_device_parameters(cpe["acs_device_id"], {"Device.WiFi.SSID.1.SSID": "CustomerNet"})
    configuration_service.record_observed(session, tenant_id, cpe["device"].id,
                                          parameters={"Device.WiFi.SSID.1.SSID": "CustomerNet"}, actor="test")
    session.commit()
    drift = configuration_service.detect_drift(session, tenant_id, cpe["device"].id, actor="test")
    assert drift is not None
    assert drift.classification in ("USER_CHANGE", "SECURITY_CRITICAL")
    assert "Device.WiFi.SSID.1.SSID" in drift.mismatched_parameters


def test_cancel_job(session, tenant_id, cpe):
    job = configuration_service.create_configuration_job(session, tenant_id, cpe["device"].id,
                                                          parameters=_params(), requested_by="test")
    job = configuration_service.cancel_job(session, tenant_id, job.id, reason="superseded", actor="test")
    assert job.state == "CANCELLED"


def test_unsupported_parameter_rejected(session, tenant_id, cpe):
    from app.services import profile_service

    profile = profile_service.create_profile(session, tenant_id, code="BADPARAM", name="Bad")
    session.commit()
    version = profile_service.create_version(session, tenant_id, profile.id, definition={
        "VENDOR_ONLY_PARAM": {"value": "x"}}, actor="test")
    session.commit()
    with pytest.raises(ConfigurationError):
        configuration_service.create_configuration_job(session, tenant_id, cpe["device"].id,
                                                       profile_version_id=version.id, requested_by="test")
