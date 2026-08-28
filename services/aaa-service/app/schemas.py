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
class TenantIn(StrictModel):
    name: str = Field(min_length=1, max_length=128); enabled: bool = True; policy: dict[str, Any] = Field(default_factory=dict)
class IpPoolIn(StrictModel):
    tenant_id: UUID
    name: str = Field(min_length=1, max_length=128)
    address_family: Literal["ipv4", "ipv6"] = "ipv4"
    cidr: str
    excluded: list[str] = Field(default_factory=list, max_length=1024)
class RadiusServerIn(StrictModel):
    name: str = Field(min_length=1, max_length=128)
    host: str = Field(min_length=1, max_length=255)
    internal_api_key: str = Field(min_length=16, max_length=512)
    environment: str = Field(default="production", max_length=32)
    region: str | None = Field(default=None, max_length=64)
    auth_port: int = Field(default=1812, ge=1, le=65535)
    accounting_port: int = Field(default=1813, ge=1, le=65535)
    coa_port: int = Field(default=3799, ge=1, le=65535)
class HeartbeatIn(StrictModel):
    version_metadata: dict[str, Any] = Field(default_factory=dict)
class PasswordRotationIn(StrictModel):
    password: str = Field(min_length=8, max_length=512)
class CoAIn(StrictModel):
    idempotency_key: str = Field(min_length=1, max_length=255)
    attributes: dict[str, str | int] = Field(default_factory=dict, max_length=16)
