"""Tenant-scoping enforcement for the Assurance Service.

Tenant-owned models require a validated TenantContext. Access without context
fails closed. Platform-aggregate views require explicit PLATFORM scope."""
from __future__ import annotations

from .context import get_context
from .domain.exceptions import TenantContextRequiredError, TenantIsolationError

_TENANT_OWNED: set = set()


def tenant_owned(model) -> None:
    _TENANT_OWNED.add(model)


def is_tenant_owned(model) -> bool:
    return model in _TENANT_OWNED


def enforce_scope(tenant_id) -> None:
    """Fail closed: tenant-scoped reads/writes require a matching context."""
    ctx = get_context()
    if ctx is None or ctx.tenant_id is None:
        raise TenantContextRequiredError("tenant context required for tenant-owned data")
    if str(ctx.tenant_id) != str(tenant_id):
        raise TenantIsolationError("tenant isolation violation")


def require_platform_aggregate() -> None:
    ctx = get_context()
    if ctx is None or ctx.scope_kind != "PLATFORM_AGGREGATE":
        from .domain.exceptions import UnauthorizedAggregateError
        raise UnauthorizedAggregateError("platform aggregate access requires explicit authorization")
