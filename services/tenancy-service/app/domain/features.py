"""Tenant quota evaluation."""
from __future__ import annotations


def quota_allows(limit: float | None, used: float, requested: float = 0.0) -> bool:
    if limit is None:
        return True
    return (used + requested) <= limit
