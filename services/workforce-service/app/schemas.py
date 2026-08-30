"""Workforce pydantic schemas."""
import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class TechnicianIn(BaseModel):
    name: str
    phone: str | None = None
    email: str | None = None
    skills: list[str] = Field(default_factory=list)
    territories: list[str] = Field(default_factory=list)


class TechnicianOut(BaseModel):
    id: uuid.UUID
    name: str
    phone: str | None
    email: str | None
    status: str
    skills: list
    territories: list
    rating: float
    joined_at: datetime

    model_config = {"from_attributes": True}


class WorkOrderIn(BaseModel):
    title: str
    type: str = "INSTALLATION"
    customer_id: str | None = None
    address: str | None = None
    priority: str = "MEDIUM"
    source_ticket_id: str | None = None
    sla_minutes: int | None = Field(None, ge=15)


class WorkOrderOut(BaseModel):
    id: uuid.UUID
    ref_id: str
    tenant_id: uuid.UUID
    title: str
    type: str
    customer_id: str | None
    address: str | None
    status: str
    priority: str
    technician_id: uuid.UUID | None
    source_ticket_id: str | None
    sla_deadline: datetime | None
    created_at: datetime
    completed_at: datetime | None

    model_config = {"from_attributes": True}


class TransitionIn(BaseModel):
    transition: str
    note: str | None = None


class AssignIn(BaseModel):
    technician_id: uuid.UUID
    scheduled_start: datetime | None = None
    scheduled_end: datetime | None = None
    notes: str | None = None


class DispatchIn(BaseModel):
    notes: str | None = None


class LocationIn(BaseModel):
    technician_id: uuid.UUID
    lat: float
    lon: float
    work_order_id: uuid.UUID | None = None


class ProofIn(BaseModel):
    work_order_id: uuid.UUID | None = None
    kind: str = "PHOTO"
    evidence_key: str
    visit_id: uuid.UUID | None = None


class SiteCheckIn(BaseModel):
    kind: str = "SITE_FEASIBILITY"
    passed: bool = True
    details: dict = Field(default_factory=dict)


class ChecklistValidateIn(BaseModel):
    completed: list[str] = Field(default_factory=list)


class FeedbackIn(BaseModel):
    rating: int = Field(..., ge=1, le=5)
    comment: str | None = None


class EscalationIn(BaseModel):
    work_order_id: uuid.UUID
    level: str = "LEVEL_1"
    reason: str | None = None


class InventoryItemIn(BaseModel):
    item_type: str
    serial_number: str | None = None
    mac_address: str | None = None


class ConsumableIn(BaseModel):
    name: str
    sku: str
    quantity: int = 0
    low_threshold: int = 5


class ConsumeIn(BaseModel):
    work_order_id: uuid.UUID
    sku: str
    quantity: int = Field(..., ge=1)


class IssueIn(BaseModel):
    work_order_id: uuid.UUID
    technician_id: uuid.UUID | None = None


class ShiftIn(BaseModel):
    technician_id: uuid.UUID
    start_time: datetime
    end_time: datetime


class HandoverIn(BaseModel):
    accepted_by: str | None = None
    notes: str | None = None
