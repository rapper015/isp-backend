from typing import Any, Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field

class StrictModel(BaseModel): model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
class RadiusRequest(StrictModel):
    attributes: dict[str, Any] = Field(default_factory=dict, max_length=64)
    correlation_id: str | None = Field(default=None, max_length=64)
    idempotency_key: str | None = Field(default=None, max_length=255)
class AuthenticationRequest(RadiusRequest): pass
class AuthorizationRequest(RadiusRequest): pass
class AccountingRequest(RadiusRequest): pass
class PostAuthRequest(RadiusRequest): pass
class RadiusResponse(StrictModel):
    outcome: Literal["Access-Accept", "Access-Reject", "OK", "Temporary-Failure"]
    decision: str
    reply_attributes: dict[str, str | int] = Field(default_factory=dict)
    control_attributes: dict[str, str | int] = Field(default_factory=dict)
    correlation_id: str
class CredentialIn(StrictModel):
    tenant_id: UUID; subscriber_id: UUID; username: str = Field(min_length=1, max_length=128); password: str = Field(min_length=8, max_length=512)
    allowed_methods: list[Literal["pap", "chap", "mschapv2", "mac"]] = ["pap"]
    mac_address: str | None = None
class NasIn(StrictModel):
    tenant_id: UUID; name: str = Field(min_length=1, max_length=128); source_ip: str; nas_identifier: str | None = Field(default=None, max_length=128)
    allowed_services: list[Literal["pppoe", "hotspot", "mac"]] = ["pppoe", "hotspot"]
    allowed_methods: list[Literal["pap", "chap", "mschapv2", "mac"]] = ["pap"]
class NasUpdateIn(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    nas_identifier: str | None = Field(default=None, max_length=128)
    allowed_services: list[Literal["pppoe", "hotspot", "mac"]] | None = None
    allowed_methods: list[Literal["pap", "chap", "mschapv2", "mac"]] | None = None
    source_cidr: str | None = Field(default=None, max_length=50)
    vendor: str | None = Field(default=None, max_length=64)
    device_type: str | None = Field(default=None, max_length=64)
    auth_port: int | None = Field(default=None, ge=1, le=65535)
    accounting_port: int | None = Field(default=None, ge=1, le=65535)
    coa_port: int | None = Field(default=None, ge=1, le=65535)
    radius_group_id: UUID | None = None
    capabilities: dict[str, Any] | None = None
class TenantIn(StrictModel):
    name: str = Field(min_length=1, max_length=128); enabled: bool = True; policy: dict[str, Any] = Field(default_factory=dict)
class IpPoolIn(StrictModel):
    tenant_id: UUID
    name: str = Field(min_length=1, max_length=128)
    address_family: Literal["ipv4", "ipv6"] = "ipv4"
    cidr: str
    excluded: list[str] = Field(default_factory=list, max_length=1024)
class IpReservationIn(StrictModel):
    subscriber_id: UUID
    address: str = Field(min_length=1, max_length=64)
class RadiusServerIn(StrictModel):
    name: str = Field(min_length=1, max_length=128)
    host: str = Field(min_length=1, max_length=255)
    internal_api_key: str = Field(min_length=16, max_length=512)
    environment: str = Field(default="production", max_length=32)
    region: str | None = Field(default=None, max_length=64)
    auth_port: int = Field(default=1812, ge=1, le=65535)
    accounting_port: int = Field(default=1813, ge=1, le=65535)
    coa_port: int = Field(default=3799, ge=1, le=65535)
class RadiusServerUpdateIn(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    environment: str | None = Field(default=None, max_length=32)
    region: str | None = Field(default=None, max_length=64)
    group_id: UUID | None = None
    draining: bool | None = None
    priority: int | None = Field(default=None, ge=0, le=10000)
    weight: int | None = Field(default=None, ge=0, le=10000)
    notes: str | None = Field(default=None, max_length=4000)
    version_metadata: dict[str, Any] | None = None
class RadiusServerGroupIn(StrictModel):
    name: str = Field(min_length=1, max_length=128)
    tenant_id: UUID | None = None
    region: str | None = Field(default=None, max_length=64)
    minimum_healthy: int = Field(default=1, ge=1, le=100)
    failover_policy: dict[str, Any] = Field(default_factory=dict, max_length=32)
class RadiusServerGroupUpdateIn(StrictModel):
    region: str | None = Field(default=None, max_length=64)
    minimum_healthy: int | None = Field(default=None, ge=1, le=100)
    enabled: bool | None = None
    failover_policy: dict[str, Any] | None = Field(default=None, max_length=32)
class HeartbeatIn(StrictModel):
    version_metadata: dict[str, Any] = Field(default_factory=dict)
class PasswordRotationIn(StrictModel):
    password: str = Field(min_length=8, max_length=512)
class CoAIn(StrictModel):
    idempotency_key: str = Field(min_length=1, max_length=255)
    attributes: dict[str, str | int] = Field(default_factory=dict, max_length=16)
class CredentialUpdateIn(StrictModel):
    allowed_methods: list[Literal["pap", "chap", "mschapv2", "mac"]] | None = None
    mac_address: str | None = None
    expires_at: str | None = Field(default=None, max_length=64)
    status: Literal["active", "disabled", "revoked"] | None = None
class PolicyPreviewIn(StrictModel):
    nas_id: UUID | None = None
    overrides: dict[str, Any] = Field(default_factory=dict, max_length=64)
class SessionReconcileIn(StrictModel):
    nas_id: UUID
    active_session_ids: list[str] = Field(default_factory=list, max_length=10000)
