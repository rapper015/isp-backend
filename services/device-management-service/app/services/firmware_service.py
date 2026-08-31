"""Firmware repository, approval, compatibility, phased rollouts (canary),
deployments and verification. Never launch fleet-wide as the first stage;
never claim rollback support the device does not provide."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain import firmware as fw_rules
from ..domain.exceptions import FirmwareError, NotFoundError, RolloutError, StateTransitionError, ValidationError
from ..integrations.acs import get_acs_client
from ..models import (
    DeviceModel,
    DeviceModelVariant,
    FirmwareApproval,
    FirmwareArtifact,
    FirmwareCompatibility,
    FirmwareCohort,
    FirmwareDeployment,
    FirmwareException,
    FirmwareRollout,
    FirmwareRolloutStage,
    FirmwareVerification,
    ManagedCpe,
)
from ..state_machine import firmware_deployment_transition, rollout_stage_transition, rollout_transition
from . import catalog_service, device_service
from .audit_service import append_event, audit, correlation, outbox


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _env_int(name: str, default: int) -> int:
    from os import getenv

    try:
        return int(getenv(name, str(default)))
    except ValueError:
        return default


def get_artifact_or_404(session: Session, tenant_id, artifact_id: uuid.UUID) -> FirmwareArtifact:
    artifact = session.get(FirmwareArtifact, artifact_id)
    if artifact is None or artifact.tenant_id != tenant_id:
        raise NotFoundError("firmware artifact not found")
    return artifact


def upload_firmware(session: Session, tenant_id, *, vendor: str, model: str, version: str,
                    checksum_sha256: str, data: bytes, product_class: str | None = None,
                    hardware_version: str | None = None, release_notes: str | None = None,
                    uploaded_by: str = "system", actor: str = "system", storage_ref: str | None = None,
                    correlation_id: str | None = None) -> FirmwareArtifact:
    request_id = correlation(correlation_id)
    if not fw_rules.validate_checksum(data, checksum_sha256):
        raise FirmwareError("firmware checksum does not match file contents")
    existing = session.scalars(select(FirmwareArtifact).where(
        FirmwareArtifact.tenant_id == tenant_id, FirmwareArtifact.vendor == vendor,
        FirmwareArtifact.model == model, FirmwareArtifact.version == version,
        FirmwareArtifact.file_type == "FIRMWARE")).first()
    if existing is not None:
        raise FirmwareError("a firmware artifact with the same vendor/model/version already exists")
    artifact = FirmwareArtifact(
        tenant_id=tenant_id, vendor=vendor, model=model, product_class=product_class,
        hardware_version=hardware_version, version=version, file_type="FIRMWARE",
        file_size=len(data), checksum_sha256=checksum_sha256.lower(), signature_status="UNSIGNED",
        storage_ref=storage_ref or f"firmware/{tenant_id}/{uuid.uuid4().hex}.bin",
        release_notes=release_notes, approval_state="UPLOADED", uploaded_by=uploaded_by,
        correlation_id=request_id)
    session.add(artifact)
    session.flush()
    audit(session, tenant_id, "device.firmware.uploaded", "device_firmware_artifacts", str(artifact.id),
          actor=actor, payload={"vendor": vendor, "model": model, "version": version}, correlation_id=request_id)
    return artifact


def approve_firmware(session: Session, tenant_id, artifact_id: uuid.UUID, *, decision: str,
                     reviewed_by: str = "system", reason: str | None = None, actor: str = "system") -> FirmwareArtifact:
    artifact = get_artifact_or_404(session, tenant_id, artifact_id)
    decision = decision.upper()
    if decision not in ("APPROVED", "REJECTED"):
        raise ValidationError("decision must be APPROVED or REJECTED")
    artifact.approval_state = decision
    if decision == "APPROVED":
        artifact.approved_by = reviewed_by
        artifact.approved_at = _now()
    session.add(FirmwareApproval(tenant_id=tenant_id, artifact_id=artifact.id, decision=decision,
                                 reviewed_by=reviewed_by, reason=reason))
    session.flush()
    audit(session, tenant_id, "device.firmware.approval", "device_firmware_artifacts", str(artifact.id),
          actor=actor, payload={"decision": decision, "reason": reason})
    return artifact


def define_compatibility(session: Session, tenant_id, artifact_id: uuid.UUID, *, model_variant_id: uuid.UUID,
                         min_current_version: str | None = None, max_current_version: str | None = None,
                         verified: bool = False, actor: str = "system") -> FirmwareCompatibility:
    get_artifact_or_404(session, tenant_id, artifact_id)
    row = session.scalars(select(FirmwareCompatibility).where(
        FirmwareCompatibility.artifact_id == artifact_id,
        FirmwareCompatibility.model_variant_id == model_variant_id)).first()
    if row is None:
        row = FirmwareCompatibility(tenant_id=tenant_id, artifact_id=artifact_id,
                                    model_variant_id=model_variant_id)
        session.add(row)
    row.min_current_version = min_current_version
    row.max_current_version = max_current_version
    row.verified = verified
    session.flush()
    return row


def create_rollout(session: Session, tenant_id, *, artifact_id: uuid.UUID, name: str, strategy: str,
                   policy: dict, created_by: str = "system", actor: str = "system",
                   correlation_id: str | None = None) -> FirmwareRollout:
    request_id = correlation(correlation_id)
    artifact = get_artifact_or_404(session, tenant_id, artifact_id)
    if artifact.approval_state != "APPROVED":
        raise RolloutError("firmware artifact is not approved")
    errors = fw_rules.validate_rollout_policy(policy)
    if errors:
        raise RolloutError("; ".join(errors))
    rollout = FirmwareRollout(tenant_id=tenant_id, artifact_id=artifact.id, name=name,
                              strategy=strategy.upper(), state="DRAFT", policy=policy,
                              created_by=created_by, correlation_id=request_id)
    session.add(rollout)
    session.flush()
    return rollout


def build_rollout_stages(session: Session, tenant_id, rollout_id: uuid.UUID, *, fleet_size: int,
                         actor: str = "system") -> list[FirmwareRolloutStage]:
    rollout = session.get(FirmwareRollout, rollout_id)
    if rollout is None or rollout.tenant_id != tenant_id:
        raise NotFoundError("rollout not found")
    stages = list(session.scalars(select(FirmwareRolloutStage).where(
        FirmwareRolloutStage.rollout_id == rollout.id).order_by(FirmwareRolloutStage.stage_number)))
    if stages:
        return stages
    policy = rollout.policy
    stage_percentages = policy.get("stage_percentages") or [1, 5, 10, 25, 59]
    created = []
    for index, percentage in enumerate(stage_percentages, start=1):
        stage = FirmwareRolloutStage(
            tenant_id=tenant_id, rollout_id=rollout.id, stage_number=index,
            stage_name=f"stage-{index}", size=fw_rules.compute_stage_size(
                rollout.strategy, fleet_size, index, percentage),
            percentage=percentage, state="PENDING",
            observation_period_minutes=policy.get("observation_period_minutes", 60),
            success_threshold=float(policy.get("success_threshold", 0.95)),
            failure_threshold=float(policy.get("failure_threshold", 0.05)),
            requires_manual_approval=policy.get("requires_manual_approval", index == 1))
        session.add(stage)
        created.append(stage)
    session.flush()
    return created


def start_rollout(session: Session, tenant_id, rollout_id: uuid.UUID, *, actor: str = "system") -> FirmwareRollout:
    rollout = session.get(FirmwareRollout, rollout_id)
    if rollout is None or rollout.tenant_id != tenant_id:
        raise NotFoundError("rollout not found")
    if rollout.state == "DRAFT":
        _rtransition(rollout, "READY")
    _rtransition(rollout, "RUNNING")
    rollout.started_at = _now()
    session.flush()
    return rollout


def _rtransition(rollout: FirmwareRollout, target: str) -> None:
    try:
        rollout_transition(rollout.state, target)
    except ValueError as error:
        raise StateTransitionError(str(error)) from error
    rollout.state = target


def queue_deployment(session: Session, tenant_id, *, rollout_id: uuid.UUID, cpe_id: uuid.UUID,
                     stage_id: uuid.UUID | None = None, actor: str = "system",
                     correlation_id: str | None = None) -> FirmwareDeployment:
    request_id = correlation(correlation_id)
    rollout = session.get(FirmwareRollout, rollout_id)
    if rollout is None or rollout.tenant_id != tenant_id:
        raise NotFoundError("rollout not found")
    if rollout.state not in ("RUNNING", "READY"):
        raise RolloutError(f"rollout is not running (state {rollout.state})")
    device = device_service.get_device_or_404(session, tenant_id, cpe_id)
    artifact = session.get(FirmwareArtifact, rollout.artifact_id)
    compat = session.scalars(select(FirmwareCompatibility).where(
        FirmwareCompatibility.artifact_id == artifact.id,
        FirmwareCompatibility.model_variant_id == device.model_variant_id)).first()
    if compat is None and not _compat_matches(session, artifact, device):
        raise RolloutError("device model/hardware is not compatible with this firmware")
    deployment = FirmwareDeployment(
        tenant_id=tenant_id, cpe_id=cpe_id, rollout_id=rollout.id, stage_id=stage_id,
        artifact_id=artifact.id, previous_firmware=device.firmware_version, state="QUEUED",
        correlation_id=request_id)
    session.add(deployment)
    session.flush()
    append_event(session, device, "firmware.upgrade_queued", payload={"deployment_id": str(deployment.id),
                                                                      "target": artifact.version},
                 actor_type="agent", actor_id=actor, correlation_id=request_id)
    outbox(session, "cpe.firmware_upgrade_started.v1", tenant_id, request_id,
           {"cpe_id": str(device.id), "deployment_id": str(deployment.id), "target_version": artifact.version})
    session.flush()
    return deployment


def _compat_matches(session: Session, artifact: FirmwareArtifact, device: ManagedCpe) -> bool:
    model_name = device.model_name
    if not model_name and device.model_variant_id:
        variant = session.get(DeviceModelVariant, device.model_variant_id)
        if variant is not None:
            model = session.get(DeviceModel, variant.model_id)
            if model is not None:
                model_name = model.model_name
    name_match = model_name is not None and artifact.model == model_name
    class_match = bool(device.product_class) and artifact.product_class == device.product_class
    hw_match = artifact.hardware_version is None or artifact.hardware_version == device.hardware_version
    return (name_match or class_match) and hw_match


def execute_deployment(session: Session, tenant_id, deployment_id: uuid.UUID, *, actor: str = "system",
                       correlation_id: str | None = None) -> FirmwareDeployment:
    deployment = session.get(FirmwareDeployment, deployment_id)
    if deployment is None or deployment.tenant_id != tenant_id:
        raise NotFoundError("firmware deployment not found")
    device = session.get(ManagedCpe, deployment.cpe_id)
    artifact = session.get(FirmwareArtifact, deployment.artifact_id)
    client = get_acs_client({"instance_id": str(device.acs_instance_id)})
    _dtransition(deployment, "CONNECTION_REQUEST_PENDING")
    task_id = client.download_file(device.acs_device_id, artifact.storage_ref, "1 Firmware Upgrade Image")
    deployment.genieacs_task_id = task_id
    outcome = client.trigger_connection_request(device.acs_device_id)
    deployment.connection_request_outcome = outcome
    deployment.started_at = _now()
    _dtransition(deployment, "TRANSFERRING")
    session.flush()
    return deployment


def _dtransition(deployment: FirmwareDeployment, target: str) -> None:
    try:
        firmware_deployment_transition(deployment.state, target)
    except ValueError as error:
        raise StateTransitionError(str(error)) from error
    deployment.state = target


def complete_deployment(session: Session, tenant_id, deployment_id: uuid.UUID, *, transferred: bool = True,
                        reported_firmware: str | None = None, health_checks: dict | None = None,
                        offline: bool = False, actor: str = "system",
                        correlation_id: str | None = None) -> FirmwareDeployment:
    deployment = session.get(FirmwareDeployment, deployment_id)
    if deployment is None or deployment.tenant_id != tenant_id:
        raise NotFoundError("firmware deployment not found")
    device = session.get(ManagedCpe, deployment.cpe_id)
    artifact = session.get(FirmwareArtifact, deployment.artifact_id)
    if offline:
        _dtransition(deployment, "WAITING_FOR_INFORM")
        session.flush()
        return deployment
    if not transferred:
        _dtransition(deployment, "FAILED")
        deployment.failure_code = "TRANSFER_FAILED"
        _finalize_failure(session, tenant_id, deployment, device, correlation_id)
        session.flush()
        return deployment
    if deployment.state == "TRANSFERRING":
        _dtransition(deployment, "TRANSFERRED")
    _dtransition(deployment, "VERIFYING")
    if reported_firmware is None:
        reported_firmware = _read_firmware(session, device)
    verified = reported_firmware == artifact.version
    session.add(FirmwareVerification(tenant_id=tenant_id, deployment_id=deployment.id, cpe_id=device.id,
                                     expected_version=artifact.version, reported_version=reported_firmware,
                                     verified=verified, health_checks=health_checks or {},
                                     verified_at=_now()))
    deployment.reported_firmware_after = reported_firmware
    if verified:
        _dtransition(deployment, "SUCCEEDED")
        deployment.completed_at = _now()
        device.firmware_version = reported_firmware
        device.firmware_compliance = "COMPLIANT"
        append_event(session, device, "firmware.upgrade_completed", payload={"deployment_id": str(deployment.id),
                                                                             "target": artifact.version},
                     actor_type="system", actor_id=actor, correlation_id=correlation_id or deployment.correlation_id)
        outbox(session, "cpe.firmware_upgrade_completed.v1", tenant_id, correlation_id or deployment.correlation_id,
               {"cpe_id": str(device.id), "deployment_id": str(deployment.id), "version": artifact.version})
    else:
        _dtransition(deployment, "FAILED")
        deployment.failure_code = "VERSION_VERIFICATION_FAILED"
        _finalize_failure(session, tenant_id, deployment, device, correlation_id)
    session.flush()
    return deployment


def _read_firmware(session: Session, device: ManagedCpe) -> str | None:
    client = get_acs_client({"instance_id": str(device.acs_instance_id)})
    family = device.data_model_family
    path = "Device.DeviceInfo.SoftwareVersion" if family == "TR181" else "InternetGatewayDevice.DeviceInfo.SoftwareVersion"
    try:
        values = client.get_parameters(device.acs_device_id, [path])
        return values.get(path)
    except Exception:  # noqa: BLE001
        return None


def _finalize_failure(session: Session, tenant_id, deployment: FirmwareDeployment, device: ManagedCpe,
                      correlation_id: str | None) -> None:
    deployment.failure_detail = deployment.failure_detail or deployment.failure_code
    append_event(session, device, "firmware.upgrade_failed", payload={"deployment_id": str(deployment.id),
                                                                      "code": deployment.failure_code},
                 actor_type="system", actor_id="worker",
                 correlation_id=correlation_id or deployment.correlation_id)
    outbox(session, "cpe.firmware_upgrade_failed.v1", tenant_id, correlation_id or deployment.correlation_id,
           {"cpe_id": str(device.id), "deployment_id": str(deployment.id), "code": deployment.failure_code})
    if fw_rules.rollback_claim_supported(_rollback_capability(session, device)):
        _dtransition(deployment, "ROLLED_BACK")
        deployment.failure_detail = "rolled back to previous firmware"


def _rollback_capability(session: Session, device: ManagedCpe) -> str:
    from ..models import DeviceModelVariant

    variant = session.get(DeviceModelVariant, device.model_variant_id) if device.model_variant_id else None
    return variant.rollback_capability if variant else "NONE"


def advance_rollout_stages(session: Session, tenant_id, rollout_id: uuid.UUID, *, actor: str = "system") -> dict:
    rollout = session.get(FirmwareRollout, rollout_id)
    if rollout is None or rollout.tenant_id != tenant_id:
        raise NotFoundError("rollout not found")
    summary = {"paused": False, "completed": False}
    if rollout.state != "RUNNING":
        return summary
    stages = list(session.scalars(select(FirmwareRolloutStage).where(
        FirmwareRolloutStage.rollout_id == rollout.id).order_by(FirmwareRolloutStage.stage_number)))
    # Promote the earliest pending stage to RUNNING when none is running yet.
    if not any(stage.state == "RUNNING" for stage in stages):
        next_pending = session.scalars(select(FirmwareRolloutStage).where(
            FirmwareRolloutStage.rollout_id == rollout.id,
            FirmwareRolloutStage.state == "PENDING").order_by(FirmwareRolloutStage.stage_number).limit(1)).first()
        if next_pending is not None:
            _stransition(next_pending, "RUNNING")
            next_pending.started_at = _now()
    for stage in stages:
        if stage.state != "RUNNING":
            continue
        successes, failures = _stage_outcomes(session, rollout.id, stage.id)
        decision = fw_rules.canary_passes(
            successes, failures, success_threshold=stage.success_threshold,
            failure_threshold=stage.failure_threshold)
        if decision == "PAUSE":
            _stransition(stage, "PAUSED")
            summary["paused"] = True
            return summary
        if decision == "COMPLETE":
            _stransition(stage, "SUCCEEDED")
            stage.completed_at = _now()
            # Advance the next pending stage.
            next_pending = session.scalars(select(FirmwareRolloutStage).where(
                FirmwareRolloutStage.rollout_id == rollout.id,
                FirmwareRolloutStage.state == "PENDING").order_by(FirmwareRolloutStage.stage_number).limit(1)).first()
            if next_pending is not None:
                _stransition(next_pending, "RUNNING")
                next_pending.started_at = _now()
            else:
                _rtransition(rollout, "COMPLETED")
                rollout.completed_at = _now()
                summary["completed"] = True
    session.flush()
    return summary


def _stage_outcomes(session: Session, rollout_id, stage_id) -> tuple[int, int]:
    rows = list(session.scalars(select(FirmwareDeployment).where(
        FirmwareDeployment.rollout_id == rollout_id, FirmwareDeployment.stage_id == stage_id)))
    successes = sum(1 for r in rows if r.state == "SUCCEEDED")
    failures = sum(1 for r in rows if r.state in ("FAILED", "ROLLED_BACK", "QUARANTINED"))
    return successes, failures


def _stransition(stage: FirmwareRolloutStage, target: str) -> None:
    try:
        rollout_stage_transition(stage.state, target)
    except ValueError as error:
        raise StateTransitionError(str(error)) from error
    stage.state = target


def pause_rollout(session: Session, tenant_id, rollout_id: uuid.UUID, *, reason: str, actor: str = "system") -> FirmwareRollout:
    rollout = session.get(FirmwareRollout, rollout_id)
    if rollout is None or rollout.tenant_id != tenant_id:
        raise NotFoundError("rollout not found")
    _rtransition(rollout, "PAUSED")
    rollout.pause_reason = reason
    audit(session, tenant_id, "device.firmware.rollout_paused", "device_firmware_rollouts", str(rollout.id),
          actor=actor, reason=reason)
    session.flush()
    return rollout


def resume_rollout(session: Session, tenant_id, rollout_id: uuid.UUID, *, actor: str = "system") -> FirmwareRollout:
    rollout = session.get(FirmwareRollout, rollout_id)
    if rollout is None or rollout.tenant_id != tenant_id:
        raise NotFoundError("rollout not found")
    _rtransition(rollout, "RUNNING")
    rollout.pause_reason = None
    session.flush()
    return rollout


def stop_rollout(session: Session, tenant_id, rollout_id: uuid.UUID, *, actor: str = "system") -> FirmwareRollout:
    rollout = session.get(FirmwareRollout, rollout_id)
    if rollout is None or rollout.tenant_id != tenant_id:
        raise NotFoundError("rollout not found")
    _rtransition(rollout, "STOPPED")
    for stage in session.scalars(select(FirmwareRolloutStage).where(
            FirmwareRolloutStage.rollout_id == rollout.id, FirmwareRolloutStage.state == "PENDING")):
        stage.state = "SKIPPED"
    session.flush()
    return rollout
