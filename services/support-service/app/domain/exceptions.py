"""Domain exceptions for the support bounded context."""
from __future__ import annotations


class SupportError(Exception):
    """Base class for expected domain errors (mapped to HTTP 4xx)."""

    status_code = 400
    code = "support_error"

    def __init__(self, message: str, *, code: str | None = None, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code


class ValidationError(SupportError):
    code = "validation_error"


class StateTransitionError(ValidationError):
    code = "invalid_transition"


class NotFoundError(SupportError):
    code = "not_found"
    status_code = 404


class PermissionDeniedError(SupportError):
    code = "permission_denied"
    status_code = 403


class TenantIsolationError(PermissionDeniedError):
    code = "tenant_isolation"


class DuplicateError(SupportError):
    code = "duplicate"
    status_code = 409


class SLAError(ValidationError):
    code = "sla_error"


class AssignmentError(ValidationError):
    code = "assignment_error"


class ActionError(SupportError):
    code = "action_error"


class IdempotencyConflict(DuplicateError):
    """A command with the same idempotency key already produced a result."""
