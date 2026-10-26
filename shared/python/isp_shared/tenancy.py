"""Tenant scope validation shared by authenticated BSS/OSS requests."""
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, event, inspect, select
from sqlalchemy.orm import with_loader_criteria


def tenant_uuid(value):
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(422, "invalid tenant_id") from exc


async def validate_tenant_scope(request, claims):
    """Check every supplied scope, not query OR body. Wildcards stay tenant-bound."""
    claimed = claims.get("tenant_id") or claims.get("tenantId")
    if claimed:
        allowed = {tenant_uuid(claimed)}
    elif "*" in claims.get("permissions", []):
        allowed = None
    else:
        access = claims.get("effective_access") or {}
        allowed = {tenant_uuid(item) for item in access.get("tenant_ids", [])}
        if not allowed:
            raise HTTPException(403, "tenant access could not be resolved")
    supplied = []
    for name in ("tenant_id", "tenantId"):
        supplied.extend(request.query_params.getlist(name))
        if request.path_params.get(name):
            supplied.append(request.path_params[name])
    if "application/json" in request.headers.get("content-type", ""):
        try:
            body = await request.json()
        except ValueError:
            body = None  # FastAPI retains responsibility for malformed JSON.
        if isinstance(body, dict):
            supplied.extend(body[name] for name in ("tenant_id", "tenantId") if body.get(name))
    requested = {tenant_uuid(value) for value in supplied if value is not None}
    if len(requested) > 1:
        raise HTTPException(422, "tenant_id values in the request must match")
    if allowed is not None and not requested.issubset(allowed):
        raise HTTPException(403, "tenant access denied")
    return allowed


def scope_session(session, base, principal):
    """Apply authenticated scope to ORM reads, bulk mutations and flushes.

    Called for each fresh request session. Worker/internal sessions intentionally
    stay unrestricted; public portal endpoints retain their explicit ownership
    queries. This does not authorize arbitrary SQL text or cross-service IDs.
    """
    if not principal or "allowed_tenant_ids" not in principal:
        return
    allowed = principal["allowed_tenant_ids"]
    session.info["allowed_tenant_ids"] = allowed
    if allowed is None:
        return
    ids = tuple(allowed)
    models = {mapper.local_table: mapper.class_ for mapper in base.registry.mappers}
    scoped = [model for model in models.values() if hasattr(model, "tenant_id")]
    tenant_models = [model for model in models.values() if model.__tablename__.endswith("_tenants")]
    criteria = {model: model.tenant_id.in_(ids) for model in scoped}
    criteria.update({model: model.id.in_(ids) for model in tenant_models})
    for model in models.values():
        if model in criteria:
            continue
        parents = []
        for column in inspect(model).columns:
            for fk in column.foreign_keys:
                parent = models.get(fk.column.table)
                if parent in scoped or parent in tenant_models:
                    parents.append(getattr(model, column.key).in_(select(getattr(parent, fk.column.key)).where(criteria[parent])))
        if parents:
            criteria[model] = and_(*parents)

    def restrict(execution):
        if execution.is_select:
            statement = execution.statement
            for model, condition in criteria.items():
                statement = statement.options(with_loader_criteria(model, condition, include_aliases=True))
            execution.statement = statement
        elif execution.is_update or execution.is_delete:
            model = execution.bind_arguments.get("mapper")
            if model is None or model.class_ not in criteria:
                raise HTTPException(403, "bulk mutations require a tenant-validated operation")
            if execution.is_update:
                protected = {c.key for c in model.columns if c.foreign_keys} | {"tenant_id"}
                if any(getattr(key, "key", key) in protected for key in (execution.statement._values or {})):
                    raise HTTPException(403, "bulk ownership changes are not permitted")
            execution.statement = execution.statement.where(criteria[model.class_])

    def validate_flush(db, flush_context, instances):
        for item in list(db.new) + list(db.dirty) + list(db.deleted):
            if hasattr(type(item), "tenant_id"):
                history = inspect(item).attrs.tenant_id.history
                if item not in db.new and history.has_changes():
                    raise HTTPException(403, "tenant ownership cannot be changed")
                value = getattr(item, "tenant_id", None)
                if value is None and item in db.new and len(ids) == 1:
                    item.tenant_id = ids[0]
                elif value not in ids:
                    raise HTTPException(403, "tenant access denied")
            if type(item) in tenant_models and item.id not in ids:
                raise HTTPException(403, "tenant access denied")
            # Reject local FK references to another tenant even if the child has
            # its own valid tenant_id. External references need service validation.
            for column in inspect(type(item)).columns:
                value = getattr(item, column.key, None)
                if value is None:
                    continue
                for fk in column.foreign_keys:
                    parent = models.get(fk.column.table)
                    if parent not in scoped:
                        continue
                    match = next((obj for obj in db.new if isinstance(obj, parent) and getattr(obj, fk.column.key) == value), None)
                    if match is None:
                        with db.no_autoflush:
                            match = db.scalar(select(parent).where(getattr(parent, fk.column.key) == value))
                    if match is not None and match in db.new and match.tenant_id is None and len(ids) == 1:
                        match.tenant_id = ids[0]
                    if match is None or match.tenant_id not in ids:
                        raise HTTPException(404, "related record not found")

    event.listen(session, "do_orm_execute", restrict)
    event.listen(session, "before_flush", validate_flush)
