"""Domain exceptions for the device-management bounded context."""
from __future__ import annotations


class DeviceMgmtError(Exception):
    status_code = 400
    code = "device_error"

    def __init__(self, message: str, *, code: str | None = None, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code


class ValidationError(DeviceMgmtError):
    code = "validation_error"


class StateTransitionError(ValidationError):
    code = "invalid_transition"


class NotFoundError(DeviceMgmtError):
    code = "not_found"
    status_code = 404


class PermissionDeniedError(DeviceMgmtError):
    code = "permission_denied"
    status_code = 403


class TenantIsolationError(PermissionDeniedError):
    code = "tenant_isolation"


class DuplicateError(DeviceMgmtError):
    code = "duplicate"
    status_code = 409


class IdempotencyConflict(DuplicateError):
    """A command with the same idempotency key already produced a result."""


class DeviceClaimError(ValidationError):
    code = "device_claim_error"


class AmbiguousOwnershipError(DeviceClaimError):
    code = "ambiguous_ownership"


class CapabilityError(ValidationError):
    code = "capability_error"


class ProfileError(ValidationError):
    code = "profile_error"


class ConfigurationError(ValidationError):
    code = "configuration_error"


class VerificationError(ConfigurationError):
    code = "verification_failed"


class DriftError(ConfigurationError):
    code = "drift_detected"


class ActionError(ValidationError):
    code = "action_error"


class AuthorizationRequiredError(ActionError):
    code = "authorization_required"


class DiagnosticError(ValidationError):
    code = "diagnostic_error"


class FirmwareError(ValidationError):
    code = "firmware_error"


class RolloutError(FirmwareError):
    code = "rollout_error"


class SSRFProtectionError(ValidationError):
    code = "ssrf_protection"


class SecretAccessError(PermissionDeniedError):
    code = "secret_access_denied"


class ACSUnavailableError(DeviceMgmtError):
    code = "acs_unavailable"
    status_code = 502


class ACSTaskError(DeviceMgmtError):
    code = "acs_task_error"


class TelemetryError(ValidationError):
    code = "telemetry_error"
