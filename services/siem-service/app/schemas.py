"""SIEM pydantic request/response schemas."""
import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class SecurityEventIn(BaseModel):
    event_type: str = Field(..., max_length=120)
    category: str = "OTHER"
    severity: str = "MEDIUM"
    source_ip: str | None = None
    actor: str | None = None
    target: str | None = None
    payload: dict = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    event_time: datetime | None = None


class SecurityEventOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    event_type: str
    category: str
    severity: str
    source_ip: str | None
    actor: str | None
    target: str | None
    payload: dict
    masked_payload: dict
    tags: list[str]
    event_time: datetime
    received_at: datetime
    digest: str
    prev_hash: str | None
    block_index: int
    archived: bool

    model_config = {"from_attributes": True}


class EvidenceOut(BaseModel):
    block_index: int
    prev_hash: str | None
    payload_hash: str
    root_hash: str
    created_at: datetime

    model_config = {"from_attributes": True}


class PolicyIn(BaseModel):
    name: str
    category: str = "COMPLIANCE"
    description: str | None = None
    rule_json: dict = Field(default_factory=dict)
    severity: str = "HIGH"
    enabled: bool = True


class PolicyOut(PolicyIn):
    id: uuid.UUID
    tenant_id: uuid.UUID
    created_by: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ViolationOut(BaseModel):
    id: uuid.UUID
    policy_id: uuid.UUID
    event_id: uuid.UUID | None
    description: str
    severity: str
    status: str
    detected_at: datetime
    resolved_at: datetime | None

    model_config = {"from_attributes": True}


class RetentionIn(BaseModel):
    data_class: str
    retention_days: int = Field(..., ge=1)
    action: str = "PURGE"
    enabled: bool = True
    regulatory_ref: str | None = None


class ConsentIn(BaseModel):
    subscriber_id: str
    purpose: str
    status: str = "GRANTED"
    source: str | None = None


class ConsentOut(BaseModel):
    id: uuid.UUID
    subscriber_id: str
    purpose: str
    status: str
    source: str | None
    granted_at: datetime
    revoked_at: datetime | None

    model_config = {"from_attributes": True}


class DataRequestIn(BaseModel):
    requester_id: str
    subject_id: str
    request_type: str
    payload: dict = Field(default_factory=dict)


class DataRequestOut(BaseModel):
    id: uuid.UUID
    requester_id: str
    subject_id: str
    request_type: str
    status: str
    created_at: datetime
    fulfilled_at: datetime | None

    model_config = {"from_attributes": True}


class CaseIn(BaseModel):
    title: str
    category: str = "INCIDENT"
    severity: str = "MEDIUM"
    assignee: str | None = None
    linked_event_ids: list[uuid.UUID] = Field(default_factory=list)


class CaseOut(BaseModel):
    id: uuid.UUID
    ref_id: str
    tenant_id: uuid.UUID
    title: str
    category: str
    severity: str
    status: str
    assignee: str | None
    impact_score: float
    priority_score: float
    escalated: bool
    breach_impact: dict
    notification_tracked: bool
    opened_at: datetime
    closed_at: datetime | None

    model_config = {"from_attributes": True}


class TransitionIn(BaseModel):
    transition: str
    note: str | None = None


class BreachNotifyIn(BaseModel):
    case_id: uuid.UUID
    channel: str = "EMAIL"
    audience: str = "REGULATOR"
    message: str | None = None


class VulnerabilityIn(BaseModel):
    target: str
    scanner: str | None = None
    severity: str = "MEDIUM"
    cve: str | None = None
    details: dict = Field(default_factory=dict)


class LIRequestIn(BaseModel):
    target_subscriber: str
    requester: str
    authority_ref: str | None = None


class LIDecideIn(BaseModel):
    decision: str  # APPROVED | REJECTED
    approver_note: str | None = None
