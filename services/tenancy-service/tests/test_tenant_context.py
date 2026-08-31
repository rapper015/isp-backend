"""Tenant-context + fail-closed isolation tests (M8 section 52)."""
import uuid

import pytest

from app.context import TenantContext, get_context, require_tenant, reset_context, resolve, set_context
from app.domain.exceptions import (
    TenantContextConflictError,
    TenantContextRequiredError,
    TenantIsolationError,
)
from app.routing import DatabaseRouter, default_router, is_tenant_owned


def test_missing_context_fails_closed(session, tenant_id):
    from app.models import Partner

    # No tenant context set -> tenant-owned model access raises.
    assert get_context() is None
    with pytest.raises(TenantContextRequiredError):
        require_tenant()
    assert is_tenant_owned(Partner) is True
    router = default_router()
    with pytest.raises(TenantContextRequiredError):
        router.db_for_read(Partner)


def test_context_immutable_and_cleared():
    ctx = TenantContext(tenant_id=uuid.uuid4(), scope_kind="TENANT")
    token = set_context(ctx)
    assert get_context() == ctx
    reset_context(token)
    assert get_context() is None


def test_conflicting_tenant_signals_rejected():
    ctx = TenantContext(tenant_id=uuid.uuid4())
    token = set_context(ctx)
    try:
        with pytest.raises(TenantContextConflictError):
            resolve(uuid.uuid4())
    finally:
        reset_context(token)


def test_supplied_tenant_must_match_context():
    ctx = TenantContext(tenant_id=uuid.uuid4())
    token = set_context(ctx)
    try:
        assert resolve(str(ctx.tenant_id)).tenant_id == ctx.tenant_id
    finally:
        reset_context(token)


def test_router_never_falls_back_for_tenant_owned(session, tenant_b):
    router = DatabaseRouter(databases={"control": {"isolation_mode": "SHARED_SCHEMA_WITH_RLS", "ready": True}})
    # db_alias_for a database-per-tenant alias that isn't provisioned must fail.
    with pytest.raises(TenantIsolationError):
        router.db_alias_for(uuid.uuid4(), isolation_mode="DATABASE_PER_TENANT")


def test_cross_tenant_write_blocked(session, tenant_id, tenant_b, make_partner):
    from app.models import Partner

    # Tenant A creates a partner; Tenant B router refuses a cross-tenant write to it.
    partner = make_partner()
    router = default_router()
    with pytest.raises(TenantIsolationError):
        router.assert_no_cross_tenant_write([partner], tenant_id=tenant_b.id)


def test_scope_expansion_denied():
    from app.domain.identity import scope_expands

    assert scope_expands("BRANCH", "BRANCH") is True
    assert scope_expands("BRANCH", "TENANT") is False  # cannot widen scope
    assert scope_expands("FRANCHISE", "BRANCH") is True
    assert scope_expands("TENANT", "FRANCHISE") is True


def test_worker_task_has_explicit_scope_not_ambient(session, tenant):
    # Worker tasks set their own explicit context; after they finish it is cleared.
    from app.tasks import run_tenant_tasks

    result = run_tenant_tasks(session, tenant.id)
    assert result["tenant_id"] == str(tenant.id)
    assert get_context() is None  # no leakage after the task


def test_tenant_owned_registry_complete():
    from app.models import Partner, TenantConfiguration

    assert is_tenant_owned(Partner) and is_tenant_owned(TenantConfiguration)
