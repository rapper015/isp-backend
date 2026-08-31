"""Database-routing layer for the Tenancy Service.

The control plane owns one database (shared schema, `ten_` tables). Tenant
isolation tiers are modeled explicitly. The router FAILS CLOSED: any
tenant-owned model access without a validated TenantContext raises a controlled
exception — it never falls back to a default database and never returns an
empty queryset because context is missing.

For the `DATABASE_PER_TENANT` tier, `db_alias_for()` resolves a per-tenant alias
from the TenantDatabase registry; the alias must exist and be READY or the call
fails. For the implemented shared-schema tier, all tenant-owned tables live in
the control database and are still gated by tenant context + scoped selectors."""
from __future__ import annotations

import uuid

from .context import get_context
from .domain.exceptions import TenantContextRequiredError, TenantIsolationError

CONTROL_DB = "control"

# Model classes whose data is tenant-owned and therefore REQUIRES a TenantContext.
_TENANT_OWNED: set = set()


def tenant_owned(model) -> None:
    """Register a model as tenant-owned (used to fail closed on access)."""
    _TENANT_OWNED.add(model)


def is_tenant_owned(model) -> bool:
    return model in _TENANT_OWNED


class DatabaseRouter:
    """Interface used by selectors/repositories to pick the target database and
    to verify a tenant context before touching tenant-owned data."""

    name = "tenancy_router"

    def __init__(self, *, databases: dict[str, dict] | None = None):
        # alias -> {"isolation_mode": ..., "ready": bool}
        self.databases = databases or {CONTROL_DB: {"isolation_mode": "SHARED_SCHEMA_WITH_RLS", "ready": True}}

    def db_alias_for(self, tenant_id: uuid.UUID | str, *, isolation_mode: str | None = None) -> str:
        tenant = uuid.UUID(str(tenant_id))
        mode = (isolation_mode or self.databases.get(CONTROL_DB, {}).get("isolation_mode", "SHARED_SCHEMA_WITH_RLS"))
        if mode in ("SHARED_SCHEMA_WITH_RLS", "SCHEMA_PER_TENANT", "DEDICATED_DEPLOYMENT"):
            # These tiers keep the control/operational DB shared; context gating is applied.
            return CONTROL_DB
        # DATABASE_PER_TENANT: resolve the tenant-specific alias (must be provisioned + ready).
        alias = f"tenant_{str(tenant).replace('-', '')}"
        info = self.databases.get(alias)
        if info is None or not info.get("ready"):
            raise TenantIsolationError("tenant database is not provisioned or not ready; refusing fallback")
        return alias

    def db_for_read(self, model=None, *, tenant_context=None):
        if model is not None and is_tenant_owned(model):
            ctx = tenant_context or get_context()
            if ctx is None:
                raise TenantContextRequiredError("tenant-owned read requires tenant context")
        return CONTROL_DB

    def db_for_write(self, model=None, *, tenant_context=None):
        if model is not None and is_tenant_owned(model):
            ctx = tenant_context or get_context()
            if ctx is None:
                raise TenantContextRequiredError("tenant-owned write requires tenant context")
        return CONTROL_DB

    def db_for_migration(self, *, tenant_id: uuid.UUID | None = None):
        if tenant_id is not None:
            return self.db_alias_for(tenant_id)
        return CONTROL_DB

    def assert_no_cross_tenant_write(self, rows, *, tenant_id) -> None:
        """Prevent one tenant's transaction from writing another tenant's rows."""
        tid = uuid.UUID(str(tenant_id))
        for row in rows:
            row_tenant = getattr(row, "tenant_id", None)
            if row_tenant is not None and uuid.UUID(str(row_tenant)) != tid:
                raise TenantIsolationError("cross-tenant write blocked")


def default_router() -> DatabaseRouter:
    return DatabaseRouter()
