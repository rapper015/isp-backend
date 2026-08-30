"""Tenant-scope enforcement + audit for Workforce routes (fail-closed)."""
from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Query, Session

from . import models
from .context import TenantContext


def enforce_scope(query: Query, table, ctx: TenantContext) -> Query:
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


def record_audit(session: Session, ctx: TenantContext, action: str, resource: str,
                 resource_id: str | None = None, outcome: str = "SUCCESS",
                 detail: dict | None = None, source_ip: str | None = None):
    """Append-only audit record (immutable)."""
    session.add(models.WorkforceAuditLog(
        tenant_id=ctx.require_tenant() if ctx.tenant_id else None,
        actor=ctx.user_id, action=action, resource=resource, resource_id=resource_id,
        outcome=outcome, detail=detail or {}, source_ip=source_ip,
        created_at=datetime.now(timezone.utc),
    ))
