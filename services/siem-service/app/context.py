"""Tenant context (fail-closed) for the SIEM service.

Every tenant-owned resource requires an authenticated, tenant-scoped context.
PLATFORM_AGGREGATE contexts may cross tenants; everything else FAILS CLOSED."""
from dataclasses import dataclass, field
from uuid import UUID


@dataclass
class TenantContext:
    user_id: str
    role: str
    tenant_id: UUID | None = None
    permissions: set[str] = field(default_factory=set)
    scope_kind: str | None = None
    is_platform_aggregate: bool = False

    def require_tenant(self) -> UUID:
        if not self.tenant_id:
            raise PermissionError("No tenant scope")
        return self.tenant_id


def require_tenant() -> UUID:
    raise PermissionError("No tenant scope")
