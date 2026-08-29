"""Vendor-neutral versioned configuration profiles, profile assignment rules,
configuration jobs with verification, desired/observed state and drift."""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, Timestamped, UuidPk


class DeviceConfigurationProfile(Base, Timestamped, UuidPk):
    __tablename__ = "device_configuration_profiles"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_device_profile_code"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device_tenants.id"), index=True, nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(String(24), default="DRAFT", nullable=False)
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class DeviceConfigurationProfileVersion(Base, Timestamped, UuidPk):
    """Immutable published profile version."""

    __tablename__ = "device_configuration_profile_versions"
    __table_args__ = (
        UniqueConstraint("profile_id", "version", name="uq_device_profile_version"),
        Index("ix_device_profile_version_state", "state"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device_tenants.id"), index=True, nullable=False)
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device_configuration_profiles.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(24), default="DRAFT", nullable=False)
    definition: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)  # vendor-neutral parameter map
    change_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ProfileParameter(Base, Timestamped, UuidPk):
    """A single vendor-neutral parameter inside a profile version."""

    __tablename__ = "device_profile_parameters"
    __table_args__ = (UniqueConstraint("version_id", "code", name="uq_device_profile_param"),)

    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device_configuration_profile_versions.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    secret_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)  # resolved only during execution
    sensitive: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    writable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class ProfileAssignmentRule(Base, Timestamped, UuidPk):
    __tablename__ = "device_profile_assignment_rules"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device_tenants.id"), index=True, nullable=False)
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device_configuration_profiles.id"), nullable=False)
    rule_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    facts: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)  # model/hw/firmware/plan/region/... matchers
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class ProfileAssignmentDecision(Base, Timestamped, UuidPk):
    """Every profile selection records the input facts, rule version, selection
    and reason — decisions are explainable and auditable."""

    __tablename__ = "device_profile_assignment_decisions"

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device_tenants.id"), index=True, nullable=False)
    cpe_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("managed_cpes.id"), index=True, nullable=False)
    input_facts: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    rule_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    selected_profile_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    selected_profile_version_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class DeviceDesiredState(Base, Timestamped, UuidPk):
    __tablename__ = "device_desired_states"
    __table_args__ = (UniqueConstraint("cpe_id", "profile_version_id", name="uq_device_desired_state"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device_tenants.id"), index=True, nullable=False)
    cpe_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("managed_cpes.id"), nullable=False)
    profile_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("device_configuration_profile_versions.id"), nullable=True)
    compiled_parameters: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    compiled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DeviceObservedState(Base, Timestamped, UuidPk):
    __tablename__ = "device_observed_states"
    __table_args__ = (Index("ix_device_observed_cpe", "cpe_id"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device_tenants.id"), index=True, nullable=False)
    cpe_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("managed_cpes.id"), nullable=False)
    parameters: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DeviceConfigurationSnapshot(Base, Timestamped, UuidPk):
    """Historical snapshot of the exact profile version applied to a device —
    never overwritten when the profile changes."""

    __tablename__ = "device_configuration_snapshots"
    __table_args__ = (Index("ix_device_snapshot_cpe", "cpe_id"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device_tenants.id"), index=True, nullable=False)
    cpe_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("managed_cpes.id"), nullable=False)
    profile_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("device_configuration_profile_versions.id"), nullable=True)
    compiled_parameters: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ConfigurationJob(Base, Timestamped, UuidPk):
    """Controlled application of a profile/parameter change with verification."""

    __tablename__ = "device_configuration_jobs"
    __table_args__ = (Index("ix_device_cfg_job_state", "state"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device_tenants.id"), index=True, nullable=False)
    cpe_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("managed_cpes.id"), index=True, nullable=False)
    job_type: Mapped[str] = mapped_column(String(40), default="APPLY_PROFILE", nullable=False)
    state: Mapped[str] = mapped_column(String(32), default="DRAFT", nullable=False)
    profile_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("device_configuration_profile_versions.id"), nullable=True)
    desired_parameters: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    diff_preview: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    verification_required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    requested_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    timeout_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    rollback_of_job_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), index=True, nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ConfigurationStep(Base, Timestamped, UuidPk):
    __tablename__ = "device_configuration_steps"
    __table_args__ = (Index("ix_device_cfg_step_job", "job_id"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device_tenants.id"), index=True, nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device_configuration_jobs.id"), nullable=False)
    step_type: Mapped[str] = mapped_column(String(40), nullable=False)  # SET_PARAMETER / ADD_OBJECT / DELETE_OBJECT / READBACK
    parameter_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parameter_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    desired_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(String(24), default="PENDING", nullable=False)
    genieacs_task_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    response_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    fault_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fault_detail: Mapped[str | None] = mapped_column(Text, nullable=True)


class ConfigurationVerification(Base, Timestamped, UuidPk):
    __tablename__ = "device_configuration_verifications"
    __table_args__ = (Index("ix_device_cfg_verify_job", "job_id"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device_tenants.id"), index=True, nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device_configuration_jobs.id"), nullable=False)
    cpe_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("managed_cpes.id"), nullable=False)
    state: Mapped[str] = mapped_column(String(24), default="PENDING", nullable=False)
    desired_parameters: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    observed_parameters: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    mismatches: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    sensitive_unreadable: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ConfigurationDrift(Base, Timestamped, UuidPk):
    __tablename__ = "device_configuration_drift"
    __table_args__ = (Index("ix_device_drift_cpe", "cpe_id"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device_tenants.id"), index=True, nullable=False)
    cpe_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("managed_cpes.id"), nullable=False)
    classification: Mapped[str] = mapped_column(String(32), default="UNKNOWN", nullable=False)
    mismatched_parameters: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    policy: Mapped[str] = mapped_column(String(32), default="REPORT_ONLY", nullable=False)
    severity: Mapped[str] = mapped_column(String(16), default="INFO", nullable=False)
    action_taken: Mapped[str | None] = mapped_column(String(255), nullable=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
