"""Pydantic schemas for Milestone 3 network-control endpoints."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class PolicyCreate(BaseModel):
    tenant_id: uuid.UUID
    code: str
    name: str
    description: str | None = None
    body: dict = Field(default_factory=dict)
    effective_from: datetime | None = None


class PolicyVersionCreate(BaseModel):
    tenant_id: uuid.UUID
    body: dict
    notes: str | None = None
    effective_from: datetime | None = None
    actor: str = "system"


class PolicySchedule(BaseModel):
    tenant_id: uuid.UUID
    effective_from: datetime
    actor: str = "system"


class PolicyAssign(BaseModel):
    tenant_id: uuid.UUID
    policy_version_id: uuid.UUID
    source: str = "subscriber"
    actor: str = "system"


class OverrideCreate(BaseModel):
    tenant_id: uuid.UUID
    body: dict
    reason: str | None = None
    expires_at: datetime | None = None
    actor: str = "system"


class ExplainRequest(BaseModel):
    tenant_id: uuid.UUID
    nas_id: uuid.UUID | None = None
    facts: dict = Field(default_factory=dict)


class ControlActionCreate(BaseModel):
    tenant_id: uuid.UUID
    action_type: str  # COA | DISCONNECT
    trigger: str = "operator"
    nas_id: uuid.UUID
    session_id: uuid.UUID | None = None
    subscriber_id: uuid.UUID | None = None
    username: str | None = None
    session_identifier: dict = Field(default_factory=dict)
    requested_attributes: dict = Field(default_factory=dict)
    idempotency_key: str
    actor: str = "system"
    correlation_id: str | None = None


class ControlOutcome(BaseModel):
    tenant_id: uuid.UUID
    outcome: str  # ACK | NAK | TIMEOUT
    detail: dict = Field(default_factory=dict)
    latency_ms: int | None = None


class ReconcileRequest(BaseModel):
    tenant_id: uuid.UUID
    nas_id: uuid.UUID
    router_session_ids: list[str] = Field(default_factory=list)
    suspended_subscriber_ids: list[uuid.UUID] = Field(default_factory=list)


class ManagedApply(BaseModel):
    tenant_id: uuid.UUID
    nas_id: uuid.UUID
    policy_version_id: uuid.UUID
    objects: list[dict] = Field(default_factory=list)
    actor: str = "system"


class ManagedRead(BaseModel):
    tenant_id: uuid.UUID
    nas_id: uuid.UUID


class FupReset(BaseModel):
    tenant_id: uuid.UUID
    actor: str = "fup-operator"


class FupTopUp(BaseModel):
    tenant_id: uuid.UUID
    topup_bytes: int
    actor: str = "topup"


class BandwidthProfileCreate(BaseModel):
    tenant_id: uuid.UUID
    code: str
    name: str
    upload_kbps: int | None = None
    download_kbps: int | None = None
    upload_min_kbps: int | None = None
    download_min_kbps: int | None = None
    burst_upload_kbps: int | None = None
    burst_download_kbps: int | None = None
    burst_threshold_bytes: int | None = None
    burst_duration_seconds: int | None = None
    priority: str = "normal"
    queue_type: str = "default"


class TrafficClassCreate(BaseModel):
    tenant_id: uuid.UUID
    code: str
    name: str
    dscp: str | None = None
    protocol: str | None = None
    src_group: str | None = None
    dst_group: str | None = None
    src_port: str | None = None
    dst_port: str | None = None
    priority: int = 0
    cir_kbps: int | None = None
    mir_kbps: int | None = None
    packet_mark: str | None = None
    connection_mark: str | None = None
    queue_discipline: str = "default"
    congestion_action: str = "drop"


class QosProfileCreate(BaseModel):
    tenant_id: uuid.UUID
    code: str
    name: str
    tier: str = "standard"
    traffic_class_codes: list[str] = Field(default_factory=list)
    params: dict = Field(default_factory=dict)


class FupPolicyCreate(BaseModel):
    tenant_id: uuid.UUID
    code: str
    name: str
    cycle: str = "monthly"
    thresholds: list[dict] = Field(default_factory=list)
    reset_rule: str = "cycle_start"
    grace_bytes: int = 0
    timezone: str = "UTC"
