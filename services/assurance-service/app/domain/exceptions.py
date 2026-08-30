"""Domain exceptions for the Assurance Service."""
from __future__ import annotations


class AssuranceError(Exception):
    status_code = 400
    code = "assurance_error"

    def __init__(self, message: str, *, code: str | None = None, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code


class ValidationError(AssuranceError):
    status_code = 422
    code = "validation_error"


class NotFoundError(AssuranceError):
    status_code = 404
    code = "not_found"


class DuplicateError(AssuranceError):
    status_code = 409
    code = "duplicate"


class StateTransitionError(AssuranceError):
    status_code = 409
    code = "invalid_state_transition"


class PermissionDeniedError(AssuranceError):
    status_code = 403
    code = "permission_denied"


class ElevatedPermissionRequiredError(AssuranceError):
    status_code = 403
    code = "elevated_permission_required"


class TenantContextRequiredError(AssuranceError):
    status_code = 422
    code = "tenant_context_required"


class TenantContextConflictError(AssuranceError):
    status_code = 409
    code = "tenant_context_conflict"


class TenantIsolationError(AssuranceError):
    status_code = 403
    code = "tenant_isolation_violation"


class UnauthorizedAggregateError(AssuranceError):
    status_code = 403
    code = "platform_aggregate_requires_authorization"


class SloError(AssuranceError):
    status_code = 422
    code = "slo_error"


class SloImmutableError(SloError):
    status_code = 409
    code = "published_slo_immutable"


class AlertError(AssuranceError):
    status_code = 422
    code = "alert_error"


class CardinalityError(AssuranceError):
    status_code = 422
    code = "metric_cardinality_violation"


class IncidentError(AssuranceError):
    status_code = 422
    code = "incident_error"


class RootCauseError(AssuranceError):
    status_code = 422
    code = "root_cause_error"


class MaintenanceError(AssuranceError):
    status_code = 422
    code = "maintenance_error"
