"""Domain exceptions for the Intelligence Service."""
from __future__ import annotations


class IntelligenceError(Exception):
    status_code = 400
    code = "INTELLIGENCE_ERROR"

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code


class ValidationError(IntelligenceError):
    status_code = 422
    code = "VALIDATION"


class NotFoundError(IntelligenceError):
    status_code = 404
    code = "NOT_FOUND"


class DuplicateError(IntelligenceError):
    status_code = 409
    code = "DUPLICATE"


class StateTransitionError(IntelligenceError):
    status_code = 409
    code = "STATE_TRANSITION"


class PermissionDeniedError(IntelligenceError):
    status_code = 403
    code = "PERMISSION_DENIED"


class ElevatedPermissionRequiredError(IntelligenceError):
    status_code = 403
    code = "ELEVATED_PERMISSION_REQUIRED"


class TenantContextRequiredError(IntelligenceError):
    status_code = 422
    code = "TENANT_CONTEXT_REQUIRED"


class TenantContextConflictError(IntelligenceError):
    status_code = 409
    code = "TENANT_CONTEXT_CONFLICT"


class TenantIsolationError(IntelligenceError):
    status_code = 403
    code = "TENANT_ISOLATION"


class UnauthorizedAggregateError(IntelligenceError):
    status_code = 403
    code = "PLATFORM_AGGREGATE_UNAUTHORIZED"


class ContractError(IntelligenceError):
    status_code = 422
    code = "CONTRACT_VIOLATION"


class DataQualityError(IntelligenceError):
    status_code = 422
    code = "DATA_QUALITY"


class FeatureError(IntelligenceError):
    status_code = 422
    code = "FEATURE_ERROR"


class ModelError(IntelligenceError):
    status_code = 422
    code = "MODEL_ERROR"


class ModelImmutableError(IntelligenceError):
    status_code = 409
    code = "MODEL_IMMUTABLE"


class ModelArtifactError(IntelligenceError):
    status_code = 422
    code = "MODEL_ARTIFACT"


class PredictionError(IntelligenceError):
    status_code = 422
    code = "PREDICTION_ERROR"


class RemediationError(IntelligenceError):
    status_code = 422
    code = "REMEDIATION_ERROR"


class RemediationSafetyError(IntelligenceError):
    status_code = 403
    code = "REMEDIATION_SAFETY"


class KillSwitchEngagedError(IntelligenceError):
    status_code = 423
    code = "KILL_SWITCH"


class ApprovalError(IntelligenceError):
    status_code = 422
    code = "APPROVAL"


class CrossTenantActionError(IntelligenceError):
    status_code = 403
    code = "CROSS_TENANT_ACTION_BLOCKED"


class CardinalityError(IntelligenceError):
    status_code = 422
    code = "CARDINALITY"
