"""Process-local worker runner for AAA maintenance and command delivery.

It deliberately does not manage FreeRADIUS.  A deployment must explicitly enable
the Python-to-NAS CoA adapter; otherwise queued commands fail safely and visibly.
"""
import asyncio
from os import getenv
from time import sleep
from .commands import DisabledRadiusCommandAdapter, PyradCommandAdapter
from .database import SessionLocal
from .workers import detect_stale_sessions, evaluate_radius_server_health, flush_outbox, process_radius_command
from .events import consume_command_once, declare_topology


def adapter():
    if getenv("AAA_ENABLE_RADIUS_COMMANDS", "false").lower() == "true":
        return PyradCommandAdapter(timeout=float(getenv("AAA_RADIUS_COMMAND_TIMEOUT", "2")), retries=int(getenv("AAA_RADIUS_COMMAND_RETRIES", "2")))
    return DisabledRadiusCommandAdapter()


def run_once() -> dict[str, int | str | None]:
    """Run bounded maintenance work once; useful to a scheduler and unit tests."""
    session = SessionLocal()
    try:
        asyncio.run(declare_topology())
        command_adapter = adapter()
        return {
            "stale_sessions": detect_stale_sessions(session),
            "radius_health_changes": evaluate_radius_server_health(session),
            "outbox_published": flush_outbox(session),
            "command_status": asyncio.run(consume_command_once(lambda command_session, command_id: process_radius_command(command_session, command_adapter, command_id))) or asyncio.run(consume_command_once(lambda command_session, command_id: process_radius_command(command_session, command_adapter, command_id), queue_name="aaa.coa")),
        }
    finally:
        session.close()


def main() -> None:
    interval = max(1, int(getenv("AAA_WORKER_INTERVAL_SECONDS", "5")))
    while True:
        try:
            run_once()
        except Exception:
            # The next bounded cycle retries transient infrastructure failures.
            # Individual command attempts remain capped in process_radius_command.
            pass
        sleep(interval)


if __name__ == "__main__":
    main()
