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

# --- Milestone 0: operator/user auth ----------------------------------------
class UserCreateIn(StrictModel):
    username: str = Field(min_length=3, max_length=128)
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=255)
    email: str | None = Field(default=None, max_length=255)
    role: str = Field(default="READ_ONLY", max_length=64)
    tenant_id: UUID | None = None

class LoginIn(StrictModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=128)
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
class QuotaResetIn(StrictModel):
    idempotency_key: str = Field(min_length=1, max_length=255)
    period: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")
class NasDraftIn(StrictModel):
    tenant_id: UUID
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=500)
    site: str | None = Field(default=None, max_length=128)
    management_host: str = Field(min_length=1, max_length=253)
    management_port: int = Field(default=8729, ge=1, le=65535)
    management_protocol: Literal["api", "api_ssl"] = "api_ssl"
    api_mode: Literal["auto", "legacy", "new"] = "auto"
    tls_verify: bool = True
    tls_settings: dict[str, Any] = Field(default_factory=dict, max_length=16)
    routeros_username: str = Field(min_length=1, max_length=128)
    routeros_password: str = Field(min_length=1, max_length=512)
    radius_source_ip: str = Field(min_length=1, max_length=64)
    radius_source_ipv6: str | None = Field(default=None, max_length=64)
    nas_identifier: str | None = Field(default=None, max_length=128)
    short_name: str | None = Field(default=None, max_length=64)
    vendor: str = Field(default="mikrotik", max_length=64)
    model: str | None = Field(default=None, max_length=64)
    radius_group_id: UUID | None = None
    services: list[Literal["pppoe", "hotspot", "login", "wireless", "dot1x", "ipsec", "dhcp"]] = Field(default_factory=list)
class NasUpdateManagementIn(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=500)
    site: str | None = Field(default=None, max_length=128)
    short_name: str | None = Field(default=None, max_length=64)
    management_host: str | None = Field(default=None, max_length=253)
    management_port: int | None = Field(default=None, ge=1, le=65535)
    management_protocol: Literal["api", "api_ssl"] | None = None
    api_mode: Literal["auto", "legacy", "new"] | None = None
    tls_verify: bool | None = None
    radius_source_ip: str | None = Field(default=None, max_length=64)
    radius_source_ipv6: str | None = Field(default=None, max_length=64)
    nas_identifier: str | None = Field(default=None, max_length=128)
    model: str | None = Field(default=None, max_length=64)
    serial_number: str | None = Field(default=None, max_length=64)
    time_zone: str | None = Field(default=None, max_length=64)
    radius_group_id: UUID | None = None
    services: list[Literal["pppoe", "hotspot", "login", "wireless", "dot1x", "ipsec", "dhcp"]] | None = None
class NasCredentialIn(StrictModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=512)
    api_port: int = Field(default=8729, ge=1, le=65535)
    tls_settings: dict[str, Any] = Field(default_factory=dict, max_length=16)
    certificate_reference: str | None = Field(default=None, max_length=255)
class NasCredentialRotateIn(StrictModel):
    username: str | None = Field(default=None, min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=512)
    idempotency_key: str = Field(min_length=1, max_length=255)
class NasRadiusAssignmentIn(StrictModel):
    radius_server_id: UUID
    priority: int = Field(default=100, ge=0, le=10000)
    role: Literal["primary", "secondary"] = "secondary"
    services: list[Literal["pppoe", "hotspot", "login", "wireless", "dot1x", "ipsec", "dhcp"]] = Field(default_factory=list)
    auth_port: int | None = Field(default=None, ge=1, le=65535)
    accounting_port: int | None = Field(default=None, ge=1, le=65535)
    coa_port: int | None = Field(default=None, ge=1, le=65535)
    timeout_seconds: int = Field(default=3000, ge=500, le=60000)
    source_address: str | None = Field(default=None, max_length=64)
class NasRadiusAssignmentUpdateIn(StrictModel):
    priority: int | None = Field(default=None, ge=0, le=10000)
    role: Literal["primary", "secondary"] | None = None
    services: list[Literal["pppoe", "hotspot", "login", "wireless", "dot1x", "ipsec", "dhcp"]] | None = None
    auth_port: int | None = Field(default=None, ge=1, le=65535)
    accounting_port: int | None = Field(default=None, ge=1, le=65535)
    coa_port: int | None = Field(default=None, ge=1, le=65535)
    timeout_seconds: int | None = Field(default=None, ge=500, le=60000)
    source_address: str | None = Field(default=None, max_length=64)
    desired_status: Literal["enabled", "disabled"] | None = None
class NasHotspotProfileIn(StrictModel):
    name: str = Field(min_length=1, max_length=128)
    radius_accounting: bool = True
    location_name: str | None = Field(default=None, max_length=128)
class NasDesiredConfigurationIn(StrictModel):
    services: list[Literal["pppoe", "hotspot", "login", "wireless", "dot1x", "ipsec", "dhcp"]] = Field(default_factory=list)
    ppp_aaa: bool = False
    accounting: bool = True
    hotspot_profiles: list[NasHotspotProfileIn] = Field(default_factory=list, max_length=64)
    incoming_coa: bool = False
    coa_port: int = Field(default=3799, ge=1, le=65535)
    interim_update_seconds: int | None = Field(default=None, ge=60, le=86400)
    login_radius: bool = False
    break_glass_verified: bool = False
    acknowledge_login_risk: bool = False
    user_aaa_default_group: str | None = Field(default=None, max_length=64)
    user_aaa_excluded_groups: list[str] = Field(default_factory=list, max_length=64)
    user_aaa_accounting: bool = False
class NasPlanApplyIn(StrictModel):
    idempotency_key: str = Field(min_length=1, max_length=255)
class NasRegistrationConfirmIn(StrictModel):
    source_ip_correct: bool = False
    secret_version_applied: bool = False
    services_enabled: bool = False
    primary_configured: bool = False
    secondary_configured: bool = False
    notes: str | None = Field(default=None, max_length=2000)
class NasRegistrationVerifyIn(StrictModel):
    signal: Literal["authentication_request_observed", "accounting_request_observed", "integration_test_result", "freeradius_callback"]
    detail: dict[str, Any] = Field(default_factory=dict, max_length=16)
class NasRotationApplyIn(StrictModel):
    idempotency_key: str = Field(min_length=1, max_length=255)
class NasRollbackIn(StrictModel):
    idempotency_key: str = Field(min_length=1, max_length=255)
    reason: str | None = Field(default=None, max_length=2000)
class NasVerifyIn(StrictModel):
    idempotency_key: str = Field(min_length=1, max_length=255)
class NasReconcileIn(StrictModel):
    idempotency_key: str = Field(min_length=1, max_length=255)
    reconcile_external: bool = False
