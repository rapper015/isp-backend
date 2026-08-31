"""Domain exceptions for the Tenancy Service.

All controlled failures are `TenancyError` subclasses carrying an HTTP status
code and a stable error code. Missing tenant context FAILS CLOSED with
`TenantContextRequiredError` — never a silent fallback."""
from __future__ import annotations


class TenancyError(Exception):
    status_code = 400
    code = "tenancy_error"

    def __init__(self, message: str, *, code: str | None = None, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code


class ValidationError(TenancyError):
    status_code = 422
    code = "validation_error"


class NotFoundError(TenancyError):
    status_code = 404
    code = "not_found"


class DuplicateError(TenancyError):
    status_code = 409
    code = "duplicate"


class StateTransitionError(TenancyError):
    status_code = 409
    code = "invalid_state_transition"


class PermissionDeniedError(TenancyError):
    status_code = 403
    code = "permission_denied"


class ElevatedPermissionRequiredError(TenancyError):
    status_code = 403
    code = "elevated_permission_required"


class SeparationOfDutyError(TenancyError):
    status_code = 403
    code = "separation_of_duty_violation"


class TenantContextRequiredError(TenancyError):
    """Raised when tenant-owned data is accessed without a validated TenantContext."""
    status_code = 422
    code = "tenant_context_required"


class TenantContextConflictError(TenancyError):
    """Raised when multiple trusted tenant signals disagree."""
    status_code = 409
    code = "tenant_context_conflict"


class TenantIsolationError(TenancyError):
    status_code = 403
    code = "tenant_isolation_violation"


class TenantNotActiveError(TenancyError):
    status_code = 409
    code = "tenant_not_active"


class TenantSuspendedError(TenancyError):
    status_code = 409
    code = "tenant_suspended"


class PartnerNotActiveError(TenancyError):
    status_code = 409
    code = "partner_not_active"


class ScopeExpansionError(TenancyError):
    """Raised when an operation would silently widen authorization scope."""
    status_code = 403
    code = "scope_expansion_denied"


class CircularHierarchyError(TenancyError):
    status_code = 409
    code = "circular_hierarchy"


class FinancialError(TenancyError):
    status_code = 422
    code = "financial_error"


class LedgerError(FinancialError):
    status_code = 422
    code = "ledger_error"


class SettlementLockedError(FinancialError):
    status_code = 409
    code = "settlement_locked"


class CommissionError(FinancialError):
    status_code = 422
    code = "commission_error"


class UnsafeRuleError(CommissionError):
    status_code = 422
    code = "unsafe_rule"


class ImpersonationError(TenancyError):
    status_code = 403
    code = "impersonation_error"


class QuotaExceededError(TenancyError):
    status_code = 429
    code = "quota_exceeded"
