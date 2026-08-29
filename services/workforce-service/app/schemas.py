"""API schemas for the workforce service. Domain rules stay in services/domain;
these models only shape request/response contracts."""
from __future__ import annotations

from datetime import datetime, time
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Work orders
# ---------------------------------------------------------------------------
class WorkOrderCreate(StrictModel):
    tenant_id: UUID | None = None
    work_order_type: str
    customer_id: str | None = None
    customer_name: str | None = None
    service_subscription_id: str | None = None
    service_location_id: str | None = None
    oss_order_id: str | None = None
    oss_order_number: str | None = None
    support_ticket_id: str | None = None
    support_ticket_number: str | None = None
    nms_incident_id: str | None = None
    billing_ref: str | None = None
    franchise_id: str | None = None
    reseller_id: str | None = None
    branch_id: str | None = None
    service_area_id: UUID | None = None
    priority: str = "P3_MEDIUM"
    severity: str = "SEV3"
    latitude: float | None = None
    longitude: float | None = None
    address_line: str | None = None
    scheduled_start: datetime | None = None
    scheduled_end: datetime | None = None
    instructions: str | None = None
    source_channel: str = "API"
    strategy: str | None = None
    correlation_id: str | None = None
    idempotency_key: str | None = None


class ScheduleIn(StrictModel):
    window_start: datetime
    window_end: datetime
    customer_preferred: bool = False
    correlation_id: str | None = None


class AssignIn(StrictModel):
    strategy: str = "SKILL_BASED"
    technician_id: UUID | None = None
    reason: str | None = None
    correlation_id: str | None = None


class RejectIn(StrictModel):
    technician_id: UUID
    reason: str = Field(..., min_length=3)
    correlation_id: str | None = None


class BlockIn(StrictModel):
    blocker_type: str
    reason: str = Field(..., min_length=3)
    severity: str = "MEDIUM"
    correlation_id: str | None = None


class PartsIn(StrictModel):
    materials: list[dict] = []
    reason: str | None = None
    correlation_id: str | None = None


class CompleteIn(StrictModel):
    result_code: str
    summary: str = Field(..., min_length=3)
    root_cause_reference: str | None = None
    correlation_id: str | None = None


class ReasonIn(StrictModel):
    reason: str = Field(..., min_length=3)
    correlation_id: str | None = None


class LinkOrderIn(StrictModel):
    order_id: str
    order_number: str | None = None
    correlation_id: str | None = None


class LinkTicketIn(StrictModel):
    ticket_id: str
    ticket_number: str | None = None
    correlation_id: str | None = None


class LinkIncidentIn(StrictModel):
    incident_id: str
    correlation_id: str | None = None


class RelatedIn(StrictModel):
    relation_type: str = "LINKED"
    to_work_order_id: UUID


# ---------------------------------------------------------------------------
# Check-in / check-out
# ---------------------------------------------------------------------------
class CheckInIn(StrictModel):
    device_timestamp: datetime | None = None
    latitude: float | None = None
    longitude: float | None = None
    gps_accuracy_m: float | None = None
    exception_reason: str | None = None
    override_approved_by: str | None = None
    offline: bool = False
    network_available: bool = True
    correlation_id: str | None = None


# ---------------------------------------------------------------------------
# Technician profiles
# ---------------------------------------------------------------------------
class TechnicianCreate(StrictModel):
    user_ref: str
    name: str
    phone: str | None = None
    email: str | None = None
    employment_type: str = "EMPLOYEE"
    team_code: str | None = None
    supervisor_ref: str | None = None
    base_lat: float | None = None
    base_lng: float | None = None
    vehicle_ref: str | None = None
    max_daily_capacity: int = 4
    supported_work_order_types: list[str] = []
    service_area_ids: list[str] = []


class SkillIn(StrictModel):
    skill: str
    proficiency: int = 3


class CertificationIn(StrictModel):
    certification: str
    expires_at: str | None = None


class AvailabilityIn(StrictModel):
    available_date: str
    start_time: time | None = None
    end_time: time | None = None
    status: str = "AVAILABLE"


class ShiftIn(StrictModel):
    day_of_week: int
    start_time: time
    end_time: time


class StatusIn(StrictModel):
    status: str
    source: str = "API"
    correlation_id: str | None = None


class CertExceptionIn(StrictModel):
    certification: str
    reason: str = Field(..., min_length=3)


# ---------------------------------------------------------------------------
# Checklist / proof / QA
# ---------------------------------------------------------------------------
class ChecklistSubmitIn(StrictModel):
    responses: dict
    correlation_id: str | None = None


class ProofIn(StrictModel):
    evidence_key: str
    evidence_type: str
    file_ref: str | None = None
    checksum: str | None = None
    capture_timestamp: datetime | None = None
    latitude: float | None = None
    longitude: float | None = None
    device_ref: str | None = None
    checklist_item_code: str | None = None
    correlation_id: str | None = None


class MaterialIn(StrictModel):
    material_code: str
    quantity: int = Field(..., gt=0)
    usage_type: str = "CONSUMED"
    correlation_id: str | None = None


class DeviceIn(StrictModel):
    device_type: str = "ONT"
    serial_number: str
    mac_address: str | None = None
    service_subscription_id: str | None = None
    correlation_id: str | None = None


class AcknowledgementIn(StrictModel):
    method: str
    masked_recipient: str | None = None
    consent_text_version: str | None = None
    result: str = "CONFIRMED"
    exception: str | None = None
    correlation_id: str | None = None


class ReviewIn(StrictModel):
    reason: str | None = None
    rework: bool = True
    correlation_id: str | None = None


# ---------------------------------------------------------------------------
# Field SLA
# ---------------------------------------------------------------------------
class SLAPolicyCreate(StrictModel):
    code: str
    name: str


class SLATargetIn(StrictModel):
    priority: str = "ALL"
    kind: str = "TIME_TO_COMPLETE"
    business_seconds: int = Field(..., gt=0)


class SLAPolicyVersionCreate(StrictModel):
    definition: dict
    targets: list[SLATargetIn]
    activate: bool = False


class ActivateVersionIn(StrictModel):
    version: int


class SLAExceptionIn(StrictModel):
    arrival_deadline: datetime
    completion_deadline: datetime
    reason: str = Field(..., min_length=3)


# ---------------------------------------------------------------------------
# Offline sync / dispatch
# ---------------------------------------------------------------------------
class OfflineSyncIn(StrictModel):
    device_ref: str
    commands: list[dict]


class PlanSequenceIn(StrictModel):
    sequence: list[dict]
    expected_version: int


class ValidateAssignIn(StrictModel):
    technician_id: UUID
    window_start: datetime | None = None
    window_end: datetime | None = None
