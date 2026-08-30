"""TenantContext for the Assurance Service (fail-closed)."""
from __future__ import annotations

import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field

from .domain.exceptions import TenantContextConflictError, TenantContextRequiredError, TenantIsolationError

_current: ContextVar["TenantContext | None"] = ContextVar("assurance_context", default=None)


@dataclass(frozen=True)
class TenantContext:
    tenant_id: uuid.UUID | None
    user_id: str | None = None
    role: str | None = None
    permissions: frozenset = field(default_factory=frozenset)
    scope_kind: str = "TENANT"
    correlation_id: str | None = None
    auth_method: str = "jwt"

    def assert_can_see(self, tenant_id: uuid.UUID) -> None:
        if self.tenant_id is not None and self.tenant_id != tenant_id:
            raise TenantIsolationError("tenant isolation violation")


def set_context(ctx: TenantContext | None):
    return _current.set(ctx)


def reset_context(token) -> None:
    _current.reset(token)


def get_context() -> TenantContext | None:
    return _current.get()


def require_tenant() -> TenantContext:
    ctx = _current.get()
    if ctx is None or ctx.tenant_id is None:
        raise TenantContextRequiredError("tenant context is required for this operation")
    return ctx


def resolve(tenant_id) -> TenantContext:
    ctx = _current.get()
    supplied = uuid.UUID(str(tenant_id)) if tenant_id else None
    if ctx is None:
        if supplied is None:
            raise TenantContextRequiredError("tenant context is required")
        return TenantContext(tenant_id=supplied)
    if supplied is not None and ctx.tenant_id is not None and ctx.tenant_id != supplied:
        raise TenantContextConflictError("tenant signals disagree; request rejected")
    return ctx
