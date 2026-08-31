"""Feature-flag and entitlement evaluation. Feature flags are NOT authorization;
permissions gate actions separately."""
from __future__ import annotations


def effective_feature(platform_default: bool, tenant_override: bool | None,
                      scheduled_at=None, now=None) -> bool:
    """Resolve a feature flag for a tenant with optional scheduled activation."""
    if tenant_override is not None:
        return tenant_override
    if scheduled_at is not None and now is not None:
        return platform_default and now >= scheduled_at
    return platform_default


def quota_allows(limit: float | None, used: float, requested: float = 0.0) -> bool:
    if limit is None:
        return True
    return (used + requested) <= limit
