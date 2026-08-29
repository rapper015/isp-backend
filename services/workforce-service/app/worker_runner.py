"""Worker process entry point: periodic tasks + RabbitMQ consumption loop."""
import time
from os import getenv

from .database import SessionLocal
from . import tasks  # noqa: F401


def run_once() -> dict:
    session = SessionLocal()
    try:
        return {
            "published": tasks.flush_outbox(session),
            "sla": tasks.evaluate_field_slas(session),
            "escalations": tasks.run_escalations(session),
            "reminders": tasks.send_appointment_reminders(session),
            "stuck": len(tasks.detect_stuck_work_orders(session)),
        }
    finally:
        session.close()


def run_loop(interval_seconds: float | None = None) -> None:  # pragma: no cover - long-running
    interval = interval_seconds or float(getenv("WORKFORCE_WORKER_INTERVAL", "10"))
    while True:
        run_once()
        time.sleep(interval)


if __name__ == "__main__":
    run_loop()
