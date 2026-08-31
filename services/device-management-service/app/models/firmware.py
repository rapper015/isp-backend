"""Firmware repository, approvals, compatibility, rollouts (canary/phased),
deployments, verification and exceptions."""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, Timestamped, UuidPk


class FirmwareArtifact(Base, Timestamped, UuidPk):
    __tablename__ = "device_firmware_artifacts"
    __table_args__ = (UniqueConstraint("vendor", "model", "version", "file_type", name="uq_device_firmware_artifact"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device_tenants.id"), index=True, nullable=False)
    vendor: Mapped[str] = mapped_column(String(128), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    product_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    hardware_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    current_version_compatibility: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    downgrade_supported: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    file_type: Mapped[str] = mapped_column(String(24), default="FIRMWARE", nullable=False)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    signature_status: Mapped[str] = mapped_column(String(32), default="UNSIGNED", nullable=False)
    storage_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    release_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    known_issues: Mapped[str | None] = mapped_column(Text, nullable=True)
    approval_state: Mapped[str] = mapped_column(String(24), default="UPLOADED", nullable=False)
    uploaded_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    quarantine_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class FirmwareCompatibility(Base, Timestamped, UuidPk):
    __tablename__ = "device_firmware_compatibility"
    __table_args__ = (UniqueConstraint("artifact_id", "model_variant_id", name="uq_device_fw_compat"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device_tenants.id"), index=True, nullable=False)
    artifact_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device_firmware_artifacts.id"), nullable=False)
    model_variant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device_model_variants.id"), nullable=False)
    min_current_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    max_current_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class FirmwareApproval(Base, Timestamped, UuidPk):
    __tablename__ = "device_firmware_approvals"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device_tenants.id"), index=True, nullable=False)
    artifact_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device_firmware_artifacts.id"), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)  # APPROVED / REJECTED
    reviewed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)


class FirmwareCohort(Base, Timestamped, UuidPk):
    __tablename__ = "device_firmware_cohorts"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device_tenants.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    criteria: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)  # model/region/tenant/mac-range filters
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class FirmwareRollout(Base, Timestamped, UuidPk):
    __tablename__ = "device_firmware_rollouts"
    __table_args__ = (Index("ix_device_rollout_state", "state"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device_tenants.id"), index=True, nullable=False)
    artifact_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device_firmware_artifacts.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    strategy: Mapped[str] = mapped_column(String(32), default="CANARY", nullable=False)
    state: Mapped[str] = mapped_column(String(24), default="DRAFT", nullable=False)
    policy: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pause_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class FirmwareRolloutStage(Base, Timestamped, UuidPk):
    __tablename__ = "device_firmware_rollout_stages"
    __table_args__ = (UniqueConstraint("rollout_id", "stage_number", name="uq_device_rollout_stage"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device_tenants.id"), index=True, nullable=False)
    rollout_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device_firmware_rollouts.id"), nullable=False)
    stage_number: Mapped[int] = mapped_column(Integer, nullable=False)
    stage_name: Mapped[str] = mapped_column(String(64), nullable=False)
    size: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    percentage: Mapped[int | None] = mapped_column(Integer, nullable=True)
    state: Mapped[str] = mapped_column(String(24), default="PENDING", nullable=False)
    observation_period_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    success_threshold: Mapped[float] = mapped_column(default=0.95, nullable=False)
    failure_threshold: Mapped[float] = mapped_column(default=0.05, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    requires_manual_approval: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class FirmwareDeployment(Base, Timestamped, UuidPk):
    __tablename__ = "device_firmware_deployments"
    __table_args__ = (Index("ix_device_fw_deploy_cpe", "cpe_id"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device_tenants.id"), index=True, nullable=False)
    cpe_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("managed_cpes.id"), nullable=False)
    rollout_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device_firmware_rollouts.id"), index=True, nullable=False)
    stage_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device_firmware_rollout_stages.id"), nullable=True)
    artifact_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device_firmware_artifacts.id"), nullable=False)
    previous_firmware: Mapped[str | None] = mapped_column(String(64), nullable=True)
    state: Mapped[str] = mapped_column(String(32), default="QUEUED", nullable=False)
    genieacs_task_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    connection_request_outcome: Mapped[str] = mapped_column(String(32), default="NOT_REQUESTED", nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    transferred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reported_firmware_after: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class FirmwareVerification(Base, Timestamped, UuidPk):
    __tablename__ = "device_firmware_verifications"
    __table_args__ = (Index("ix_device_fw_verify_deploy", "deployment_id"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device_tenants.id"), index=True, nullable=False)
    deployment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device_firmware_deployments.id"), nullable=False)
    cpe_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("managed_cpes.id"), nullable=False)
    expected_version: Mapped[str] = mapped_column(String(64), nullable=False)
    reported_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    health_checks: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FirmwareException(Base, Timestamped, UuidPk):
    __tablename__ = "device_firmware_exceptions"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device_tenants.id"), index=True, nullable=False)
    cpe_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("managed_cpes.id"), nullable=False)
    rollout_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("device_firmware_rollouts.id"), nullable=True)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    granted_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
