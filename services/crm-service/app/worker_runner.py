"""Process-local worker runner for CRM background tasks."""
from os import getenv
import logging
from time import sleep

from .database import SessionLocal
from isp_shared.observability import configure_observability

configure_observability("crm-worker")
from .events import declare_topology
from .tasks import process_followups


def run_once() -> dict[str, int | None]:
    """Run bounded CRM maintenance work once; useful to a scheduler and tests."""
    import asyncio
    session = SessionLocal()
    try:
        asyncio.run(declare_topology())
        return process_followups(session)
    finally:
        session.close()


def main() -> None:
    interval = max(1, int(getenv("CRM_FOLLOWUP_WORKER_INTERVAL_SECONDS", "60")))
    while True:
        try:
            run_once()
        except Exception:
            logging.getLogger(__name__).exception("crm.worker.cycle_failed")
        sleep(interval)


if __name__ == "__main__":
    main()
