"""Pydantic request/response schemas for the OSS API."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from .enums import (
    ORDER_PRIORITIES,
    ORDER_SOURCES,
    ORDER_STATES,
    ORDER_TYPES,
    RESOURCE_TYPES,
    RESERVATION_STATES,
    SAGA_STATES,
    SERVICE_STATES,
)


class TenantMixin(BaseModel):
    tenant_id: uuid.UUID


class OrderCreate(TenantMixin):
    order_type: str = Field(..., description="one of " + ", ".join(ORDER_TYPES))
    customer_id: str | None = None
    service_location_id: str | None = None
    service_subscription_id: uuid.UUID | None = None
    requested_plan_reference: str | None = None
    previous_plan_reference: str | None = None
    requested_activation_date: datetime | None = None
    priority: str = "MEDIUM"
    source_channel: str = "API"
    franchise_id: str | None = None
    reseller_id: str | None = None
    requested_snapshot: dict = Field(default_factory=dict)
    actor: str = "system"
    idempotency_key: str | None = None
    correlation_id: str | None = None


class OrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    tenant_id: uuid.UUID
    order_number: str
    order_type: str
    state: str
    aggregate_version: int
    customer_id: str | None
    service_subscription_id: uuid.UUID | None
    service_location_id: str | None
    requested_plan_reference: str | None
    previous_plan_reference: str | None
    priority: str
    source_channel: str
    current_step: str | None
    failure_reason: str | None
    correlation_id: str | None
    requested_snapshot: dict
    created_at: datetime
    updated_at: datetime


class TransitionRequest(BaseModel):
    reason: str | None = None
    actor: str = "system"
    correlation_id: str | None = None
    idempotency_key: str | None = None


class ValidateResponse(BaseModel):
    order_id: uuid.UUID
    order_number: str
    result_state: str
    message: str


class OrderEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    aggregate_version: int
    event_type: str
    event_version: int
    actor_type: str | None
    actor_id: str | None
    correlation_id: str | None
    payload: dict
    created_at: datetime


class StatusHistoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    from_state: str
    to_state: str
    actor: str | None
    reason: str | None
    correlation_id: str | None
    created_at: datetime


class ValidActionsResponse(BaseModel):
    order_id: uuid.UUID
    state: str
    valid_actions: list[str]


class ResourceRegister(TenantMixin):
    resource_type: str
    resource_key: str
    metadata: dict = Field(default_factory=dict)


class ReservationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    resource_type: str
    resource_key: str
    order_id: uuid.UUID | None
    reservation_token: str
    state: str
    reserved_at: datetime
    expires_at: datetime | None
    allocated_at: datetime | None
    released_at: datetime | None


class CapacityResponse(BaseModel):
    capacity: dict


class SagaDetailResponse(BaseModel):
    id: uuid.UUID
    order_id: uuid.UUID
    workflow_type: str
    state: str
    current_step_index: int
    correlation_id: str | None
    failure_reason: str | None
    created_at: datetime
    updated_at: datetime


class SagaStepResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    step_index: int
    step_name: str
    state: str
    attempt_count: int
    max_attempts: int
    output: dict
    error_code: str | None
    last_error: str | None
    started_at: datetime | None
    completed_at: datetime | None


class SubscriptionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    tenant_id: uuid.UUID
    subscription_code: str
    status: str
    customer_id: str | None
    service_location_id: str | None
    plan_reference: str | None
    billing_account_reference: str | None
    order_reference: str | None
    aaa_subscriber_reference: str | None
    nas_reference: str | None
    resource_references: dict
    activation_date: datetime | None
    suspension_date: datetime | None
    termination_date: datetime | None
    created_at: datetime


class InterventionResolveRequest(BaseModel):
    resolved_by: str = "operator"
