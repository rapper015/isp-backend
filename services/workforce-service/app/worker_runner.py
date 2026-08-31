"""Workforce worker loop: SLA sweep, KPI compute, PM scheduling, outbox."""
import logging
import os
import time

from .database import SessionLocal

log = logging.getLogger("workforce.worker")
INTERVAL = float(os.getenv("WORKFORCE_WORKER_INTERVAL", "30"))


def run_once():
    from . import tasks
    session = SessionLocal()
    try:
        tasks.deliver_outbox(session)
        tasks.sweep_sla(session)
        tasks.compute_kpis(session)
        tasks.schedule_preventive_maintenance(session)
    finally:
        session.close()


def main():
    logging.basicConfig(level=logging.INFO)
    log.info("workforce worker started (interval=%ss)", INTERVAL)
    while True:
        try:
            run_once()
        except Exception as exc:  # noqa: BLE001
            log.exception("worker cycle failed: %s", exc)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
