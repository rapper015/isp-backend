"""Worker process entry point: periodic tasks + RabbitMQ consumption loop."""
import time
from os import getenv

from sqlalchemy import select

from .database import SessionLocal
from . import tasks  # noqa: F401
from .models import Tenant


def run_once() -> dict:
    session = SessionLocal()
    try:
        tenants = list(session.scalars(select(Tenant))) or []
        result = {"published": tasks.flush_outbox(session), "tenants": len(tenants)}
        for tenant in tenants:
            result["jobs"] = result.get("jobs", 0) + 1
            tasks.process_pending_jobs(session, tenant.id)
            tasks.timeout_stale_jobs(session, tenant.id)
            tasks.reconcile_drift(session, tenant.id)
            tasks.advance_firmware_rollouts(session, tenant.id)
            tasks.purge_telemetry(session, tenant.id)
        return result
    finally:
        session.close()


def run_loop(interval_seconds: float | None = None) -> None:  # pragma: no cover - long-running
    interval = interval_seconds or float(getenv("DEVICE_MGMT_WORKER_INTERVAL", "10"))
    while True:
        run_once()
        time.sleep(interval)


if __name__ == "__main__":
    run_loop()
