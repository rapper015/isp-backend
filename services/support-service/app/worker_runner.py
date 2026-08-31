"""Worker process entry point: periodic tasks + RabbitMQ consumption loop.
Runs synchronously for tests/CLI; a production deployment runs it in a
supervised process."""
import time
from os import getenv

from .database import SessionLocal
from . import tasks  # noqa: F401


def run_once() -> dict:
    session = SessionLocal()
    try:
        return {
            "published": tasks.flush_outbox(session),
            "sla": tasks.evaluate_sla_deadlines(session),
            "escalations": tasks.run_escalation_checks(session),
            "auto_closed": tasks.auto_close_resolved(session),
            "timed_out": tasks.requeue_stuck_actions(session),
            "stuck": len(tasks.detect_stuck_tickets(session)),
        }
    finally:
        session.close()


def run_loop(interval_seconds: float | None = None) -> None:  # pragma: no cover - long-running
    interval = interval_seconds or float(getenv("SUPPORT_WORKER_INTERVAL", "10"))
    while True:
        run_once()
        time.sleep(interval)


if __name__ == "__main__":
    run_loop()
