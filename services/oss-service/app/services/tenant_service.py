"""Local OSS tenant provisioning for tenant-scoped records."""
from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from ..models import Tenant


def ensure_oss_tenant(session: Session, tenant_id: uuid.UUID) -> Tenant:
    """Create the OSS tenant reference when a CRM tenant first uses OSS.

    OSS keeps its own tenant reference table because its orders, inventory, and
    workflows are protected by foreign keys. The platform tenant directory is
    authoritative, but provisioning this local reference must not require a
    separate manual database step before an operator can create an order.
    """
    tenant = session.get(Tenant, tenant_id)
    if tenant is not None:
        return tenant

    tenant = Tenant(
        id=tenant_id,
        name=f"Tenant {tenant_id}",
        code=f"OSS-{tenant_id}",
    )
    session.add(tenant)
    session.flush()
    return tenant
