"""Configuration jobs: controlled profile/parameter application with GenieACS
task semantics, read-back verification and drift detection.

A queued/created GenieACS task is never treated as successful application.
Success requires (where technically possible) task completion + parameter
read-back matching desired state."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain import parameters as param_rules
from ..domain.exceptions import (
    ConfigurationError,
    NotFoundError,
    StateTransitionError,
    ValidationError,
    VerificationError,
)
from ..enums import DRIFT_CLASSIFICATIONS, DRIFT_POLICIES
from ..integrations.acs import get_acs_client
from ..models import (
    ConfigurationDrift,
    ConfigurationJob,
    ConfigurationStep,
    ConfigurationVerification,
    DeviceConfigurationSnapshot,
    DeviceDesiredState,
    DeviceObservedState,
    ManagedCpe,
)
from ..state_machine import configuration_job_transition
from . import catalog_service, device_service, profile_service
from .audit_service import append_event, correlation, outbox


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_job_or_404(session: Session, tenant_id, job_id: uuid.UUID) -> ConfigurationJob:
    job = session.get(ConfigurationJob, job_id)
    if job is None or job.tenant_id != tenant_id:
        raise NotFoundError("configuration job not found")
    return job


def create_configuration_job(session: Session, tenant_id, cpe_id: uuid.UUID, *, profile_version_id: uuid.UUID | None = None,
                             parameters: dict | None = None, verification_required: bool = True,
                             requested_by: str = "system", actor: str = "system",
                             idempotency_key: str | None = None, correlation_id: str | None = None) -> ConfigurationJob:
    request_id = correlation(correlation_id)
    if idempotency_key:
        existing = session.scalars(select(ConfigurationJob).where(
            ConfigurationJob.tenant_id == tenant_id, ConfigurationJob.idempotency_key == idempotency_key)).first()
        if existing is not None:
            return existing
    device = device_service.get_device_or_404(session, tenant_id, cpe_id)
    if profile_version_id:
        version = profile_service.get_version_or_404(session, tenant_id, profile_version_id)
        desired, unsupported = _compile_desired(session, device, version.definition)
        if unsupported:
            raise ConfigurationError("unsupported parameters: " + ", ".join(unsupported))
    else:
        desired = dict(parameters or {})
    observed = _read_observed(session, device)
    diff = param_rules.diff_parameters({k: {"value": v, "code": k} for k, v in desired.items()}, observed,
                                       sensitive_codes=list(catalog_service.sensitive_definitions(session).keys()))
    job = ConfigurationJob(
        tenant_id=tenant_id, cpe_id=cpe_id, job_type="APPLY_PROFILE" if profile_version_id else "SET_PARAMETERS",
        state="DRAFT", profile_version_id=profile_version_id, desired_parameters=desired,
        diff_preview=diff, verification_required=verification_required, requested_by=requested_by,
        idempotency_key=idempotency_key, correlation_id=request_id)
    session.add(job)
    session.flush()
    append_event(session, device, "configuration.job_created", payload={"job_id": str(job.id)},
                 actor_type="agent", actor_id=actor, correlation_id=request_id)
    return job


def _compile_desired(session: Session, device: ManagedCpe, definition: dict):
    mappings = catalog_service.mappings_for_variant(session, device.model_variant_id) if device.model_variant_id else []
    compiled, unsupported = param_rules.compile_parameters(
        mappings, {k: v.get("value") for k, v in definition.items()},
        data_model_family=device.data_model_family)
    desired = {path: spec["value"] for path, spec in compiled.items()}
    return desired, unsupported


def _read_observed(session: Session, device: ManagedCpe) -> dict:
    latest = session.scalars(select(DeviceObservedState).where(DeviceObservedState.cpe_id == device.id)
                             .order_by(DeviceObservedState.captured_at.desc()).limit(1)).first()
    return latest.parameters if latest else {}


def record_observed(session: Session, tenant_id, cpe_id: uuid.UUID, *, parameters: dict,
                    actor: str = "system") -> DeviceObservedState:
    device = device_service.get_device_or_404(session, tenant_id, cpe_id)
    observed = DeviceObservedState(tenant_id=tenant_id, cpe_id=cpe_id, parameters=parameters, captured_at=_now())
    session.add(observed)
    session.flush()
    append_event(session, device, "configuration.observed_refreshed", payload={}, actor_type="system",
                 actor_id=actor, correlation_id=device.correlation_id)
    return observed


def approve_job(session: Session, tenant_id, job_id: uuid.UUID, *, actor: str = "system") -> ConfigurationJob:
    job = get_job_or_404(session, tenant_id, job_id)
    if job.state == "DRAFT":
        _transition(job, "VALIDATING")
    _transition(job, "READY")
    job.approved_by = actor
    job.approved_at = _now()
    session.flush()
    return job


def queue_job(session: Session, tenant_id, job_id: uuid.UUID, *, actor: str = "system",
              correlation_id: str | None = None) -> ConfigurationJob:
    job = get_job_or_404(session, tenant_id, job_id)
    _transition(job, "QUEUED")
    job.scheduled_for = _now()
    job.timeout_at = _now() + timedelta(minutes=int(_env_int("DEVICE_MGMT_JOB_TIMEOUT_MINUTES", 30)))
    device = session.get(ManagedCpe, job.cpe_id)
    append_event(session, device, "configuration.requested", payload={"job_id": str(job.id)},
                 actor_type="agent", actor_id=actor, correlation_id=correlation_id or job.correlation_id)
    outbox(session, "cpe.configuration_requested.v1", tenant_id, correlation_id or job.correlation_id,
           {"cpe_id": str(job.cpe_id), "job_id": str(job.id)})
    session.flush()
    return job


def execute_job(session: Session, tenant_id, job_id: uuid.UUID, *, actor: str = "system",
                correlation_id: str | None = None) -> ConfigurationJob:
    """Dispatch the job to GenieACS: create a task, attempt a connection
    request, then wait for the device session/inform to execute it."""
    job = get_job_or_404(session, tenant_id, job_id)
    device = session.get(ManagedCpe, job.cpe_id)
    if job.state not in ("QUEUED", "CONNECTION_REQUEST_PENDING", "WAITING_FOR_INFORM"):
        _transition(job, "QUEUED")
    client = get_acs_client({"instance_id": str(device.acs_instance_id)})
    _transition(job, "CONNECTION_REQUEST_PENDING")
    job.started_at = _now()
    outcome = "NOT_REQUESTED"
    # Create the ACS task first (durable); then attempt a connection request.
    task_id = client.set_parameters(device.acs_device_id, job.desired_parameters)
    step = ConfigurationStep(tenant_id=tenant_id, job_id=job.id, step_type="SET_PARAMETER",
                             parameter_path=", ".join(job.desired_parameters.keys()),
                             state="DISPATCHED", genieacs_task_id=task_id, desired_value="")
    session.add(step)
    session.flush()
    outcome = client.trigger_connection_request(device.acs_device_id)
    job.connection_request_outcome = outcome
    if outcome in ("UNREACHABLE", "TIMED_OUT"):
        # Device offline: keep the task safely queued; it will run on the next Inform.
        _transition(job, "WAITING_FOR_INFORM")
    else:
        _transition(job, "WAITING_FOR_INFORM")
    append_event(session, device, "configuration.dispatched",
                 payload={"job_id": str(job.id), "task_id": task_id, "connection_request_outcome": outcome},
                 actor_type="system", actor_id=actor, correlation_id=correlation_id or job.correlation_id)
    session.flush()
    return job


def _transition(job: ConfigurationJob, target: str) -> None:
    try:
        configuration_job_transition(job.state, target)
    except ValueError as error:
        raise StateTransitionError(str(error)) from error
    job.state = target


def process_task_result(session: Session, tenant_id, job_id: uuid.UUID, *, task_id: str, task_state: str,
                        task_result: dict | None = None, actor: str = "system",
                        correlation_id: str | None = None) -> ConfigurationJob:
    """Worker callback when a GenieACS task reaches a terminal/observable state.
    A completed task moves the job to verification; a fault fails the job."""
    job = get_job_or_404(session, tenant_id, job_id)
    device = session.get(ManagedCpe, job.cpe_id)
    if task_state in ("COMPLETED", "VERIFIED"):
        if job.state in ("QUEUED", "CONNECTION_REQUEST_PENDING", "WAITING_FOR_INFORM", "EXECUTING"):
            _transition(job, "EXECUTING")
        _transition(job, "DEVICE_ACKNOWLEDGED")
        step = session.scalars(select(ConfigurationStep).where(
            ConfigurationStep.job_id == job.id, ConfigurationStep.genieacs_task_id == task_id)).first()
        if step is not None:
            step.state = "DEVICE_RESPONSE"
            step.response_value = str(task_result or {})
        append_event(session, device, "configuration.device_acknowledged",
                     payload={"job_id": str(job.id), "task_id": task_id}, actor_type="system",
                     actor_id=actor, correlation_id=correlation_id or job.correlation_id)
        session.flush()
        return job
    if task_state in ("FAULTED", "VERIFICATION_FAILED"):
        _transition(job, "FAILED")
        job.failure_code = task_state
        job.failure_detail = str(task_result or {})
        append_event(session, device, "configuration.failed", payload={"job_id": str(job.id), "task_id": task_id},
                     actor_type="system", actor_id=actor, correlation_id=correlation_id or job.correlation_id)
        outbox(session, "cpe.configuration_failed.v1", tenant_id, correlation_id or job.correlation_id,
               {"cpe_id": str(job.cpe_id), "job_id": str(job.id)})
        session.flush()
        return job
    # Task still queued/pending — job stays waiting for inform.
    return job


def verify_job(session: Session, tenant_id, job_id: uuid.UUID, *, actor: str = "system",
               correlation_id: str | None = None) -> ConfigurationJob:
    """Read back the affected parameters and compare with desired state."""
    job = get_job_or_404(session, tenant_id, job_id)
    device = session.get(ManagedCpe, job.cpe_id)
    if job.state == "DEVICE_ACKNOWLEDGED":
        _transition(job, "VERIFYING")
    client = get_acs_client({"instance_id": str(device.acs_instance_id)})
    observed = client.get_parameters(device.acs_device_id, list(job.desired_parameters.keys()))
    record_observed(session, tenant_id, device.id, parameters=observed, actor=actor)
    sensitive_codes = list(catalog_service.sensitive_definitions(session).keys())
    sensitive_paths = catalog_service.sensitive_paths_for_variant(session, device.model_variant_id) \
        if device.model_variant_id else []
    result = param_rules.verify_configuration(
        {k: {"value": v, "code": k} for k, v in job.desired_parameters.items()}, observed,
        sensitive_codes=sensitive_codes, sensitive_paths=sensitive_paths,
        require_readback=job.verification_required)
    verification = ConfigurationVerification(
        tenant_id=tenant_id, job_id=job.id, cpe_id=device.id, state="VERIFIED" if result["verified"] else "DRIFT_DETECTED",
        desired_parameters=job.desired_parameters, observed_parameters=observed,
        mismatches=result["mismatched"] + result["missing"],
        sensitive_unreadable=result["sensitive_unreadable"], verified_at=_now())
    session.add(verification)
    session.flush()
    if result["verified"]:
        _transition(job, "SUCCEEDED")
        job.completed_at = _now()
        _snapshot_applied(session, tenant_id, device, job)
        device.profile_compliance = "COMPLIANT"
        append_event(session, device, "configuration.verified", payload={"job_id": str(job.id)},
                     actor_type="system", actor_id=actor, correlation_id=correlation_id or job.correlation_id)
        outbox(session, "cpe.configuration_applied.v1", tenant_id, correlation_id or job.correlation_id,
               {"cpe_id": str(job.cpe_id), "job_id": str(job.id)})
    else:
        _transition(job, "FAILED")
        job.failure_code = "VERIFICATION_FAILED"
        job.failure_detail = "; ".join(result["mismatched"] + result["missing"])
        append_event(session, device, "configuration.verification_failed", payload={"job_id": str(job.id)},
                     actor_type="system", actor_id=actor, correlation_id=correlation_id or job.correlation_id)
        outbox(session, "cpe.configuration_failed.v1", tenant_id, correlation_id or job.correlation_id,
               {"cpe_id": str(job.cpe_id), "job_id": str(job.id)})
    session.flush()
    return job


def _snapshot_applied(session: Session, tenant_id, device: ManagedCpe, job: ConfigurationJob) -> None:
    session.add(DeviceConfigurationSnapshot(
        tenant_id=tenant_id, cpe_id=device.id, profile_version_id=job.profile_version_id,
        compiled_parameters=job.desired_parameters, applied_at=_now(), correlation_id=job.correlation_id))
    if job.profile_version_id:
        desired = session.scalars(select(DeviceDesiredState).where(
            DeviceDesiredState.cpe_id == device.id,
            DeviceDesiredState.profile_version_id == job.profile_version_id)).first()
        if desired is None:
            desired = DeviceDesiredState(tenant_id=tenant_id, cpe_id=device.id,
                                         profile_version_id=job.profile_version_id,
                                         compiled_parameters=job.desired_parameters, compiled_at=_now())
            session.add(desired)
        device.current_profile_version_id = job.profile_version_id
    else:
        # Raw-parameter change: record desired state so drift detection still works.
        desired = session.scalars(select(DeviceDesiredState).where(
            DeviceDesiredState.cpe_id == device.id,
            DeviceDesiredState.profile_version_id.is_(None)).order_by(
            DeviceDesiredState.compiled_at.desc()).limit(1)).first()
        if desired is None:
            desired = DeviceDesiredState(tenant_id=tenant_id, cpe_id=device.id, profile_version_id=None,
                                         compiled_parameters=job.desired_parameters, compiled_at=_now())
            session.add(desired)
        else:
            desired.compiled_parameters = job.desired_parameters
            desired.compiled_at = _now()


def cancel_job(session: Session, tenant_id, job_id: uuid.UUID, *, reason: str, actor: str = "system") -> ConfigurationJob:
    job = get_job_or_404(session, tenant_id, job_id)
    if job.state in ("SUCCEEDED", "FAILED", "CANCELLED", "ROLLED_BACK"):
        raise ValidationError(f"cannot cancel a {job.state} job")
    _transition(job, "CANCELLED")
    job.failure_detail = reason
    session.flush()
    return job


def timeout_stale_jobs(session: Session, tenant_id, *, limit: int = 100) -> list[str]:
    stale = list(session.scalars(
        select(ConfigurationJob).where(ConfigurationJob.tenant_id == tenant_id,
                                       ConfigurationJob.state.in_(
                                           ("QUEUED", "CONNECTION_REQUEST_PENDING", "WAITING_FOR_INFORM", "EXECUTING")),
                                       ConfigurationJob.timeout_at.is_not(None),
                                       ConfigurationJob.timeout_at < _now()).limit(limit)))
    for job in stale:
        _transition(job, "TIMED_OUT")
        job.failure_code = "TIMEOUT"
        job.failure_detail = "device did not complete the task before the timeout"
        device = session.get(ManagedCpe, job.cpe_id)
        append_event(session, device, "configuration.timed_out", payload={"job_id": str(job.id)},
                     actor_type="system", actor_id="worker")
        outbox(session, "cpe.configuration_failed.v1", tenant_id, job.correlation_id,
               {"cpe_id": str(job.cpe_id), "job_id": str(job.id), "reason": "timeout"})
    session.flush()
    return [str(j.id) for j in stale]


def detect_drift(session: Session, tenant_id, cpe_id: uuid.UUID, *, policy: str = "REPORT_ONLY",
                 actor: str = "system", correlation_id: str | None = None) -> ConfigurationDrift | None:
    """Compare desired vs observed state and record a drift event when they differ."""
    device = device_service.get_device_or_404(session, tenant_id, cpe_id)
    desired_row = session.scalars(select(DeviceDesiredState).where(DeviceDesiredState.cpe_id == device.id)
                                  .order_by(DeviceDesiredState.compiled_at.desc()).limit(1)).first()
    if desired_row is None:
        return None
    observed_row = session.scalars(select(DeviceObservedState).where(DeviceObservedState.cpe_id == device.id)
                                   .order_by(DeviceObservedState.captured_at.desc()).limit(1)).first()
    observed = observed_row.parameters if observed_row else {}
    sensitive_codes = list(catalog_service.sensitive_definitions(session).keys())
    sensitive_paths = catalog_service.sensitive_paths_for_variant(session, device.model_variant_id) \
        if device.model_variant_id else []
    diff = param_rules.diff_parameters({k: {"value": v, "code": k} for k, v in desired_row.compiled_parameters.items()},
                                       observed, sensitive_codes=sensitive_codes,
                                       sensitive_paths=sensitive_paths)
    problems = diff["mismatched"] + diff["missing"]
    if not problems:
        if device.last_drift_classification != "NONE":
            device.last_drift_classification = "NONE"
            session.flush()
        return None
    classification = classify_drift(session, device, problems)
    drift = ConfigurationDrift(
        tenant_id=tenant_id, cpe_id=device.id, classification=classification,
        mismatched_parameters=problems, policy=policy, severity=_severity(classification),
        detected_at=_now())
    session.add(drift)
    device.last_drift_classification = classification
    if policy == "QUARANTINE_DEVICE":
        from ..state_machine import device_transition

        device_transition(device.state, "QUARANTINED")
        device.state = "QUARANTINED"
    append_event(session, device, "configuration.drift_detected",
                 payload={"classification": classification, "parameters": problems},
                 actor_type="system", actor_id=actor, correlation_id=correlation_id or device.correlation_id)
    outbox(session, "cpe.configuration_drift_detected.v1", tenant_id, correlation_id or device.correlation_id,
           {"cpe_id": str(device.id), "classification": classification, "parameters": problems})
    session.flush()
    return drift


def classify_drift(session: Session, device: ManagedCpe, problems: list[str]) -> str:
    return "SECURITY_CRITICAL" if any(
        p in problems for p in ("Device.ManagementServer.ConnectionRequestPassword", "InternetGatewayDevice.ManagementServer.ConnectionRequestPassword")
    ) else "USER_CHANGE"


def _severity(classification: str) -> str:
    return "HIGH" if classification == "SECURITY_CRITICAL" else "MEDIUM"


def _env_int(name: str, default: int) -> int:
    from os import getenv

    try:
        return int(getenv(name, str(default)))
    except ValueError:
        return default
