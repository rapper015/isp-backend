"""Canonical authenticated request identity and organization scope."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Any
from uuid import UUID

from fastapi import HTTPException, Request


def _uuid(value: Any, label: str) -> UUID | None:
    if value in (None, ""):
        return None
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError) as error:
        raise HTTPException(401, f"invalid {label} in authentication context") from error


def _uuid_set(values: Iterable[Any], label: str) -> frozenset[UUID]:
    result = set()
    for value in values:
        parsed = _uuid(value, label)
        if parsed is not None:
            result.add(parsed)
    return frozenset(result)


def _requested_tenant(value: UUID | str | None) -> UUID | None:
    if value in (None, ""):
        return None
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError) as error:
        raise HTTPException(422, "invalid tenant_id") from error


@dataclass(frozen=True, slots=True)
class RequestContext:
    """One representation of the caller used by every backend module.

    ``allowed_tenant_ids=None`` means platform-wide access. An empty set means
    no tenant access and is never treated as global access.
    """

    user_id: str
    tenant_id: UUID | None
    franchise_id: UUID | None
    branch_ids: frozenset[UUID]
    permissions: frozenset[str]
    roles: tuple[str, ...]
    correlation_id: str
    allowed_tenant_ids: frozenset[UUID] | None
    authentication_type: str = "jwt"

    @property
    def platform_wide(self) -> bool:
        return "*" in self.permissions and self.allowed_tenant_ids is None

    def has_permission(self, permission: str) -> bool:
        return "*" in self.permissions or permission in self.permissions

    def resolve_tenant(self, requested: UUID | str | None, *, allow_global: bool = False) -> UUID | None:
        requested_id = _requested_tenant(requested)
        if self.tenant_id is not None:
            if requested_id is not None and requested_id != self.tenant_id:
                raise HTTPException(403, "tenant access denied")
            return self.tenant_id
        if self.allowed_tenant_ids is None:
            if requested_id is None and not allow_global:
                raise HTTPException(422, "tenant_id is required")
            return requested_id
        if requested_id is not None:
            if requested_id not in self.allowed_tenant_ids:
                raise HTTPException(403, "tenant access denied")
            return requested_id
        if len(self.allowed_tenant_ids) == 1:
            return next(iter(self.allowed_tenant_ids))
        if allow_global:
            return None
        raise HTTPException(422, "tenant_id is required when more than one tenant is assigned")

    def as_legacy_principal(self) -> dict[str, Any]:
        return {
            "subject": self.user_id,
            "roles": list(self.roles),
            "permissions": sorted(self.permissions),
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "franchise_id": str(self.franchise_id) if self.franchise_id else None,
            "branch_ids": [str(value) for value in sorted(self.branch_ids, key=str)],
            "allowed_tenant_ids": self.allowed_tenant_ids,
        }


def context_from_claims(
    request: Request,
    claims: Mapping[str, Any],
    allowed_tenant_ids: set[UUID] | frozenset[UUID] | None,
) -> RequestContext:
    access = claims.get("effective_access") or {}
    roles = claims.get("roles") or ([claims["role"]] if claims.get("role") else [])
    tenant_id = _uuid(claims.get("tenant_id") or claims.get("tenantId"), "tenant_id")
    if tenant_id is not None:
        allowed: frozenset[UUID] | None = frozenset({tenant_id})
    elif allowed_tenant_ids is None:
        allowed = None
    else:
        allowed = frozenset(allowed_tenant_ids)
    return RequestContext(
        user_id=str(claims["sub"]),
        tenant_id=tenant_id,
        franchise_id=_uuid(claims.get("franchise_id") or access.get("franchise_id"), "franchise_id"),
        branch_ids=_uuid_set(claims.get("branch_ids") or access.get("branch_ids") or [], "branch_id"),
        permissions=frozenset(str(value) for value in claims.get("permissions", [])),
        roles=tuple(str(value) for value in roles),
        correlation_id=str(getattr(request.state, "correlation_id", "")),
        allowed_tenant_ids=allowed,
    )


def internal_request_context(request: Request, service_name: str) -> RequestContext:
    return RequestContext(
        user_id=service_name,
        tenant_id=None,
        franchise_id=None,
        branch_ids=frozenset(),
        permissions=frozenset({"*"}),
        roles=("INTERNAL_SERVICE",),
        correlation_id=str(getattr(request.state, "correlation_id", "")),
        allowed_tenant_ids=None,
        authentication_type="internal_service",
    )


def install_request_context(request: Request, context: RequestContext, legacy_state_name: str | None = None) -> None:
    request.state.request_context = context
    if legacy_state_name:
        setattr(request.state, legacy_state_name, context.as_legacy_principal())


def get_request_context(request: Request) -> RequestContext:
    context = getattr(request.state, "request_context", None)
    if not isinstance(context, RequestContext):
        raise HTTPException(401, "authenticated request context is unavailable")
    return context
