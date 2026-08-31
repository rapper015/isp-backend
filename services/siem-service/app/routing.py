"""Tenant-scope enforcement for SIEM routes (fail-closed)."""
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Query, Session

from . import models
from .context import TenantContext


def tenant_owned_tables() -> set[str]:
    return models.tenant_owned


def enforce_scope(query: Query, table, ctx: TenantContext) -> Query:
    """Filter a query to the tenant unless the caller is a platform aggregate."""
    if table.__tablename__ not in models.tenant_owned:
        return query
    if ctx.is_platform_aggregate:
        return query
    tenant = ctx.require_tenant()
    return query.filter(table.tenant_id == tenant)


def require_tenant_id(ctx: TenantContext) -> UUID:
    if ctx.is_platform_aggregate and not ctx.tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id required for this operation")
    return ctx.require_tenant()


def platform_scope(ctx: TenantContext) -> None:
    if not ctx.is_platform_aggregate:
        raise HTTPException(status_code=403, detail="Platform aggregate scope required")


def record_audit(session: Session, ctx: TenantContext, action: str, resource: str | None,
                 resource_id: str | None = None, outcome: str = "SUCCESS",
                 detail: dict | None = None, source_ip: str | None = None):
    """Append-only audit trail entry (features 420, 438)."""
    session.add(models.AuditLog(
        tenant_id=ctx.require_tenant() if ctx.tenant_id else None,
        actor=ctx.user_id,
        action=action,
        resource=resource,
        resource_id=resource_id,
        outcome=outcome,
        detail=detail or {},
        source_ip=source_ip,
    ))
