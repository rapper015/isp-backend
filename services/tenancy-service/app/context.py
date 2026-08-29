"""Authoritative TenantContext.

Resolved from trusted signals only (authenticated JWT tenant claim + validated
membership). Immutable for the lifetime of a request/task and carried through
contextvars so sync and async code see the same value. Any tenant-owned model
access without a context raises TenantContextRequiredError (fail closed)."""
from __future__ import annotations

import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field

from .domain.exceptions import TenantContextConflictError, TenantContextRequiredError

_current: ContextVar["TenantContext | None"] = ContextVar("tenancy_context", default=None)


@dataclass(frozen=True)
class TenantContext:
    tenant_id: uuid.UUID
    db_alias: str = "control"
    user_id: str | None = None
    membership_id: uuid.UUID | None = None
    org_unit_id: uuid.UUID | None = None
    role: str | None = None
    permissions: frozenset = field(default_factory=frozenset)
    scope_kind: str = "TENANT"
    correlation_id: str | None = None
    impersonating_user: str | None = None
    auth_method: str = "jwt"

    def assert_can_see(self, tenant_id: uuid.UUID) -> None:
        if self.tenant_id != tenant_id:
            from .domain.exceptions import TenantIsolationError
            raise TenantIsolationError("tenant isolation violation")
        if self.scope_kind == "PLATFORM_AGGREGATE":
            return


def set_context(ctx: TenantContext | None) -> TenantContext | None:
    token = _current.set(ctx)
    return token  # type: ignore[return-value]


def reset_context(token) -> None:
    _current.reset(token)


def get_context() -> TenantContext | None:
    return _current.get()


def require_tenant() -> TenantContext:
    ctx = _current.get()
    if ctx is None:
        raise TenantContextRequiredError("tenant context is required for this operation")
    return ctx


def resolve(tenant_id: uuid.UUID | str | None) -> TenantContext:
    """Reconcile a supplied tenant_id against the ambient trusted context.
    Rejects when signals conflict; fails closed when no context is present."""
    ctx = _current.get()
    supplied = uuid.UUID(str(tenant_id)) if tenant_id else None
    if ctx is None:
        if supplied is None:
            raise TenantContextRequiredError("tenant context is required")
        return TenantContext(tenant_id=supplied)
    if supplied is not None and ctx.tenant_id != supplied:
        raise TenantContextConflictError("tenant signals disagree; request rejected")
    return ctx


def context_aware(ctx: TenantContext) -> TenantContext:
    """Return the active context (used by routers/selectors to scope queries)."""
    return require_tenant() if ctx is None else ctx
