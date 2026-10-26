"""Public contract used by internal callers of OSS order provisioning."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .http import JsonServiceClient, validate_response


class CreateOrderCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: UUID
    order_type: str
    customer_id: str | None = None
    service_location_id: str | None = None
    service_subscription_id: UUID | None = None
    requested_plan_reference: str | None = None
    previous_plan_reference: str | None = None
    requested_activation_date: datetime | None = None
    priority: str = "MEDIUM"
    source_channel: str = "API"
    franchise_id: str | None = None
    reseller_id: str | None = None
    requested_snapshot: dict[str, Any] = Field(default_factory=dict)
    actor: str = "system"
    idempotency_key: str | None = Field(default=None, max_length=255)
    correlation_id: str | None = None


class OrderCreated(BaseModel):
    id: UUID
    tenant_id: UUID
    order_number: str
    order_type: str
    state: str
    customer_id: str | None = None
    service_location_id: str | None = None
    requested_plan_reference: str | None = None
    correlation_id: str | None = None


class ActivationResult(BaseModel):
    order_id: UUID
    order_number: str
    order_state: str
    workflow_id: UUID | None = None
    workflow_state: str
    subscription_id: UUID | None = None


class OssProvisioningClient:
    def __init__(self, base_url: str, api_key: str, caller: str):
        self.http = JsonServiceClient("OSS", base_url, {
            "X-Internal-API-Key": api_key,
            "X-Service-Name": caller,
        })

    def create_order(self, command: CreateOrderCommand) -> OrderCreated:
        body = self.http.request("POST", "/api/oss/orders", command.model_dump(mode="json"), timeout=15)
        return validate_response("OSS", OrderCreated, body)

    def activate_order(self, order_id: UUID | str) -> ActivationResult:
        body = self.http.request("POST", f"/api/oss/orders/{order_id}/activate", {}, timeout=30)
        return validate_response("OSS", ActivationResult, body)
