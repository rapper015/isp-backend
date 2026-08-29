"""Domain exceptions for the workforce bounded context."""
from __future__ import annotations


class WorkforceError(Exception):
    status_code = 400
    code = "workforce_error"

    def __init__(self, message: str, *, code: str | None = None, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code


class ValidationError(WorkforceError):
    code = "validation_error"


class StateTransitionError(ValidationError):
    code = "invalid_transition"


class NotFoundError(WorkforceError):
    code = "not_found"
    status_code = 404


class PermissionDeniedError(WorkforceError):
    code = "permission_denied"
    status_code = 403


class TenantIsolationError(PermissionDeniedError):
    code = "tenant_isolation"


class DuplicateError(WorkforceError):
    code = "duplicate"
    status_code = 409


class IdempotencyConflict(DuplicateError):
    """A command with the same idempotency key already produced a result."""


class AssignmentError(ValidationError):
    code = "assignment_error"


class ScheduleConflictError(ValidationError):
    code = "schedule_conflict"


class GPSValidationError(ValidationError):
    code = "gps_validation"


class ChecklistError(ValidationError):
    code = "checklist_error"


class ProofError(ValidationError):
    code = "proof_error"


class QAError(ValidationError):
    code = "qa_error"


class SLAAError(ValidationError):
    code = "sla_error"


class OfflineCommandError(WorkforceError):
    code = "offline_command_error"
