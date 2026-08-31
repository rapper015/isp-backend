"""Pydantic request schemas for the device-management service."""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DiscoverIn(StrictModel):
    tenant_id: uuid.UUID | None = None
    acs_instance_id: uuid.UUID
    acs_device_id: str
    correlation_id: str | None = None


class ResolveTenantIn(StrictModel):
    method: str
    evidence: str | None = None
    claimed_tenant_id: uuid.UUID | None = None


class ClaimIn(StrictModel):
    method: str
    evidence: str | None = None
    correlation_id: str | None = None


class AssignIn(StrictModel):
    customer_id: str | None = None
    service_subscription_id: str | None = None
    service_location_id: str | None = None
    oss_order_id: str | None = None
    work_order_id: str | None = None
    inventory_serial: str | None = None
    inventory_asset_id: str | None = None
    correlation_id: str | None = None


class TransferIn(StrictModel):
    to_tenant_id: uuid.UUID
    reason: str
    correlation_id: str | None = None


class ReasonIn(StrictModel):
    reason: str
    correlation_id: str | None = None


class ProfileCreate(StrictModel):
    code: str
    name: str
    description: str | None = None


class ProfileVersionCreate(StrictModel):
    definition: dict
    change_summary: str | None = None


class AssignmentRuleIn(StrictModel):
    facts: dict
    priority: int = 100
    reason: str | None = None


class CompilePreviewIn(StrictModel):
    model_variant_id: uuid.UUID | None = None
    data_model_family: str | None = None


class ConfigurationJobCreate(StrictModel):
    profile_version_id: uuid.UUID | None = None
    parameters: dict | None = None
    verification_required: bool = True
    requested_by: str = "system"
    idempotency_key: str | None = None
    correlation_id: str | None = None


class ObservedIn(StrictModel):
    parameters: dict


class TaskResultIn(StrictModel):
    task_id: str
    task_state: str
    task_result: dict | None = None
    correlation_id: str | None = None


class ActionCreate(StrictModel):
    action_type: str
    parameters: dict | None = None
    requested_by: str = "system"
    elevated: bool = False
    idempotency_key: str | None = None
    correlation_id: str | None = None


class ActionOutcomeIn(StrictModel):
    ok: bool
    result: dict | None = None
    correlation_id: str | None = None


class DiagnosticCreate(StrictModel):
    diagnostic_type: str
    input_parameters: dict | None = None
    requested_by: str = "system"
    support_ticket_id: str | None = None
    idempotency_key: str | None = None
    correlation_id: str | None = None


class DiagnosticResultIn(StrictModel):
    raw: dict | None = None
    offline: bool = False
    failed: bool = False
    fault_code: str | None = None
    correlation_id: str | None = None


class FirmwareUpload(StrictModel):
    vendor: str
    model: str
    version: str
    checksum_sha256: str
    product_class: str | None = None
    hardware_version: str | None = None
    release_notes: str | None = None
    storage_ref: str | None = None


class FirmwareApprovalIn(StrictModel):
    decision: str
    reviewed_by: str = "system"
    reason: str | None = None


class CompatibilityIn(StrictModel):
    model_variant_id: uuid.UUID
    min_current_version: str | None = None
    max_current_version: str | None = None
    verified: bool = False


class RolloutCreate(StrictModel):
    artifact_id: uuid.UUID
    name: str
    strategy: str = "CANARY"
    policy: dict


class StageBuildIn(StrictModel):
    fleet_size: int


class DeploymentQueueIn(StrictModel):
    cpe_id: uuid.UUID
    stage_id: uuid.UUID | None = None
    correlation_id: str | None = None


class DeploymentOutcomeIn(StrictModel):
    transferred: bool = True
    reported_firmware: str | None = None
    health_checks: dict | None = None
    offline: bool = False
    correlation_id: str | None = None


class ACSRegister(StrictModel):
    name: str
    base_url: str
    tenant_id: uuid.UUID | None = None
    environment: str = "PRODUCTION"
    cwmp_endpoint: str | None = None
    file_service_endpoint: str | None = None


class TelemetryIn(StrictModel):
    snapshot: dict


class SignalIn(StrictModel):
    signal: str
    severity: str = "INFO"
    detail: dict | None = None
    correlation_id: str | None = None
