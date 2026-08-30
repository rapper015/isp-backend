"""Reliable RabbitMQ publication from the database transaction outbox."""
import asyncio
from datetime import datetime, timezone
from os import getenv
from uuid import UUID
import aio_pika
from sqlalchemy import select
from sqlalchemy.orm import Session
from .models import ConsumerInbox, OutboxEvent

EXCHANGE = "aaa.events.v1"
RETRY_EXCHANGE = "aaa.retry.v1"
DEAD_LETTER_EXCHANGE = "aaa.dead.v1"

# Versioned NAS orchestration events published from the outbox.
NAS_EVENTS = (
    "nas.connection_test.requested.v1",
    "nas.connection_test.completed.v1",
    "nas.connection_test.failed.v1",
    "nas.discovery.requested.v1",
    "nas.discovery.completed.v1",
    "nas.discovery.failed.v1",
    "nas.configuration.plan_created.v1",
    "nas.configuration.requested.v1",
    "nas.configuration.started.v1",
    "nas.configuration.completed.v1",
    "nas.configuration.failed.v1",
    "nas.configuration.rollback_requested.v1",
    "nas.configuration.rollback_completed.v1",
    "nas.configuration.rollback_failed.v1",
    "nas.configuration.drift_detected.v1",
    "nas.health_changed.v1",
    "nas.radius_registration.generated.v1",
    "nas.radius_registration.confirmed.v1",
    "nas.radius_registration.verified.v1",
    "nas.radius_secret_rotation.requested.v1",
    "nas.radius_secret_rotation.completed.v1",
    "nas.radius_secret_rotation.failed.v1",
)

async def declare_topology() -> None:
    """Declare durable queues once per worker start; safe to call repeatedly."""
    connection = await aio_pika.connect_robust(getenv("RABBITMQ_URL", "amqp://guest:guest@127.0.0.1/"), timeout=2)
    try:
        channel = await connection.channel()
        events = await channel.declare_exchange(EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True)
        retry = await channel.declare_exchange(RETRY_EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True)
        dead = await channel.declare_exchange(DEAD_LETTER_EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True)
        for name, routing_key in (("aaa.accounting", "aaa.accounting.#"), ("aaa.commands", "aaa.disconnect.#"), ("aaa.coa", "aaa.coa.#"), ("nas.jobs", "nas.#")):
            queue = await channel.declare_queue(name, durable=True, arguments={"x-dead-letter-exchange": RETRY_EXCHANGE})
            await queue.bind(events, routing_key)
            retry_queue = await channel.declare_queue(f"{name}.retry", durable=True, arguments={"x-message-ttl": 30000, "x-dead-letter-exchange": EXCHANGE})
            await retry_queue.bind(retry, routing_key)
            dead_queue = await channel.declare_queue(f"{name}.dead", durable=True)
            await dead_queue.bind(dead, routing_key)
    finally:
        await connection.close()
def envelope(event: OutboxEvent) -> dict:
    return {"event_id": str(event.id), "event_type": event.event_type, "schema_version": 1, "occurred_at": event.occurred_at.isoformat(), "published_at": datetime.now(timezone.utc).isoformat(), "tenant_id": str(event.tenant_id) if event.tenant_id else None, "correlation_id": event.correlation_id, "causation_id": None, "idempotency_key": event.idempotency_key, "producer": "aaa-service", "trace_context": {}, "payload": event.payload}
async def _publish(messages: list[OutboxEvent]) -> None:
    connection = await aio_pika.connect_robust(getenv("RABBITMQ_URL", "amqp://guest:guest@127.0.0.1/"), timeout=2)
    try:
        channel = await connection.channel(publisher_confirms=True)
        exchange = await channel.declare_exchange(EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True)
        for item in messages:
            await exchange.publish(aio_pika.Message(body=__import__("json").dumps(envelope(item), default=str).encode(), delivery_mode=aio_pika.DeliveryMode.PERSISTENT, content_type="application/json", message_id=str(item.id)), routing_key=item.event_type)
    finally: await connection.close()
def publish_outbox(session: Session, limit: int = 100) -> int:
    pending = list(session.scalars(select(OutboxEvent).where(OutboxEvent.published_at.is_(None)).order_by(OutboxEvent.occurred_at).limit(limit)))
    if not pending: return 0
    try: asyncio.run(_publish(pending))
    except Exception:
        for item in pending: item.attempts += 1
        session.commit(); return 0
    now = datetime.now(timezone.utc)
    for item in pending: item.published_at = now; item.attempts += 1
    session.commit(); return len(pending)

async def consume_nas_job_once(processor, timeout: float = 0.25) -> str | None:
    """Consume one durable NAS job event with inbox deduplication.

    `processor` accepts a session and the NAS job UUID, applies the job, and
    records the inbox row before acknowledging so redelivery cannot run the
    same job twice.
    """
    from .database import SessionLocal
    import json
    connection = await aio_pika.connect_robust(getenv("RABBITMQ_URL", "amqp://guest:guest@127.0.0.1/"), timeout=2)
    try:
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=1)
        queue = await channel.declare_queue("nas.jobs", durable=True, arguments={"x-dead-letter-exchange": RETRY_EXCHANGE})
        message = await queue.get(timeout=timeout, fail=False)
        if message is None:
            return None
        async with message.process(requeue=False):
            try:
                payload = json.loads(message.body)
                event_id = UUID(payload["event_id"])
                job_id = UUID(payload["payload"]["job_id"])
                if not payload.get("event_type", "").startswith("nas."):
                    return "ignored"
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                return "invalid"
            session = SessionLocal()
            try:
                consumer = "aaa-nas-worker:nas.jobs"
                if session.get(ConsumerInbox, {"event_id": event_id, "consumer": consumer}):
                    return "duplicate"
                session.add(ConsumerInbox(event_id=event_id, consumer=consumer))
                session.commit()
                return processor(session, job_id) or "missing"
            finally:
                session.close()
    finally:
        await connection.close()


async def consume_command_once(processor, queue_name: str = "aaa.commands", timeout: float = 0.25) -> str | None:
    """Consume one durable CoA/Disconnect event with inbox deduplication.

    `processor` accepts a command UUID and performs the state transition.  The
    inbox is written before acknowledgement, so a redelivery cannot cause a
    second command delivery after the database has recorded the event.
    """
    from .database import SessionLocal
    import json
    connection = await aio_pika.connect_robust(getenv("RABBITMQ_URL", "amqp://guest:guest@127.0.0.1/"), timeout=2)
    try:
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=1)
        queue = await channel.declare_queue(queue_name, durable=True, arguments={"x-dead-letter-exchange": RETRY_EXCHANGE})
        message = await queue.get(timeout=timeout, fail=False)
        if message is None: return None
        async with message.process(requeue=False):
            try:
                payload = json.loads(message.body)
                event_id, command_id = UUID(payload["event_id"]), UUID(payload["payload"]["command_id"])
                if not payload.get("event_type", "").startswith(("aaa.disconnect.", "aaa.coa.")): return "ignored"
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                return "invalid"
            session = SessionLocal()
            try:
                consumer = f"aaa-command-worker:{queue_name}"
                if session.get(ConsumerInbox, {"event_id": event_id, "consumer": consumer}): return "duplicate"
                session.add(ConsumerInbox(event_id=event_id, consumer=consumer)); session.commit()
                return processor(session, command_id) or "missing"
            finally:
                session.close()
    finally:
        await connection.close()
