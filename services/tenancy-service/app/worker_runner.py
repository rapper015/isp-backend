"""Worker entrypoint: bounded run_once (testable) + supervised run_loop."""
from __future__ import annotations

import time
from os import getenv

from sqlalchemy import select

from .database import SessionLocal
from .models import Tenant
from .services import catalog_service


def run_once() -> dict:
    """One maintenance pass, strictly per-tenant (no ambient context)."""
    session = SessionLocal()
    try:
        catalog_service.ensure_defaults(session)
        session.commit()
        tenants = list(session.scalars(select(Tenant).where(
            Tenant.status.in_(("ACTIVE", "RESTRICTED")))))
        results = []
        for tenant in tenants:
            from .tasks import run_tenant_tasks

            results.append(run_tenant_tasks(session, tenant.id))
        from .tasks import flush_outbox

        flushed = flush_outbox(session)
        return {"tenants": len(tenants), "flushed": flushed, "details": results}
    finally:
        session.close()


def run_loop(interval: int | None = None) -> None:
    interval = interval or int(getenv("TENANCY_WORKER_INTERVAL", "30"))
    while True:
        try:
            run_once()
        except Exception:  # noqa: BLE001
            pass
        time.sleep(interval)


if __name__ == "__main__":
    run_loop()
