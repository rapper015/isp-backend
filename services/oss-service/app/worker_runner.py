"""Worker process entry point: periodic tasks + RabbitMQ consumption loop.
Runs synchronously for tests/CLI; a production deployment runs it in a
supervised process."""
import time
import logging
from os import getenv

from .database import SessionLocal
from isp_shared.observability import configure_observability

configure_observability("oss-worker")
from . import tasks  # noqa: F401


def run_once() -> dict:
    session = SessionLocal()
    try:
        return {
            "published": tasks.flush_outbox(session),
            "expired": tasks.expire_reservations(session),
            "stale": [str(x) for x in tasks.requeue_stale_sagas(session)],
            "advanced": tasks.advance_running_sagas(session),
        }
    finally:
        session.close()


def run_loop(interval_seconds: float | None = None) -> None:  # pragma: no cover - long-running
    interval = interval_seconds or float(getenv("OSS_WORKER_INTERVAL", "10"))
    while True:
        try:
            run_once()
        except Exception:
            logging.getLogger(__name__).exception("oss.worker.cycle_failed")
        time.sleep(interval)


if __name__ == "__main__":
    run_loop()
