"""SIEM worker loop: runs periodic tasks (retention, escalations, scans, outbox)."""
import logging
import os
import time

from .database import SessionLocal

log = logging.getLogger("siem.worker")

INTERVAL = float(os.getenv("SIEM_WORKER_INTERVAL", "30"))


def run_once():
    from . import tasks
    session = SessionLocal()
    try:
        tasks.deliver_outbox(session)
        tasks.sweep_retention(session)
        tasks.sweep_escalations(session)
        tasks.rescan_violations(session)
    finally:
        session.close()


def main():
    logging.basicConfig(level=logging.INFO)
    log.info("siem worker started (interval=%ss)", INTERVAL)
    while True:
        try:
            run_once()
        except Exception as exc:  # noqa: BLE001
            log.exception("worker cycle failed: %s", exc)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
