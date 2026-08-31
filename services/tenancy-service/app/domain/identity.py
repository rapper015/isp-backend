"""Domain rules: identity normalization, domain verification, hierarchy safety."""
from __future__ import annotations

import re
import secrets

from .exceptions import CircularHierarchyError, ValidationError

_TENANT_CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9_-]{1,38}$")


def normalize_tenant_code(code: str) -> str:
    value = (code or "").strip().upper()
    if not _TENANT_CODE_RE.match(value):
        raise ValidationError("tenant code must be 2-39 chars: uppercase letters, digits, - or _")
    return value


def normalize_domain(domain: str) -> str:
    value = (domain or "").strip().lower().rstrip(".")
    if not value or " " in value or "://" in value:
        raise ValidationError("invalid domain")
    return value


def generate_domain_token() -> str:
    return secrets.token_urlsafe(24)


def validate_hierarchy_path(parent_path: str | None, parent_tenant_id, tenant_id, parent_id) -> str:
    """Build a materialized path; prevent circular/self/cross-tenant parenting.

    parent_path is the parent's stored path (or None for a root node). tenant_id
    and parent_tenant_id must match. A child may never be its own ancestor."""
    if parent_id is not None:
        if parent_tenant_id != tenant_id:
            raise ValidationError("cross-tenant parent is not allowed")
        if str(parent_id) in (parent_path or "").split("/"):
            raise CircularHierarchyError("circular hierarchy is not allowed")
    root = str(tenant_id)
    if parent_path:
        return f"{parent_path}/{str(parent_id)}"
    return root


def is_descendant(candidate_path: str, ancestor_path: str) -> bool:
    """True if candidate is strictly under ancestor in the materialized path."""
    return candidate_path.startswith(ancestor_path.rstrip("/") + "/")


def scope_expands(scope_kind: str, requested_scope_kind: str) -> bool:
    """Deny silent scope expansion: a narrower request cannot become broader."""
    order = ["OWN", "ASSIGNED_CUSTOMERS", "BRANCH", "FRANCHISE", "DESCENDANT_ORG_UNITS", "TENANT"]
    if scope_kind == "PLATFORM_AGGREGATE":
        return requested_scope_kind == "PLATFORM_AGGREGATE"
    if requested_scope_kind not in order or scope_kind not in order:
        return scope_kind == requested_scope_kind
    return order.index(requested_scope_kind) <= order.index(scope_kind)
