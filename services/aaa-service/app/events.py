"""Reliable RabbitMQ publication from the database transaction outbox."""
import asyncio
from datetime import datetime, timezone
from os import getenv
from uuid import UUID
import aio_pika
from sqlalchemy import select
from sqlalchemy.orm import Session
from .models import OutboxEvent

EXCHANGE = "aaa.events.v1"
RETRY_EXCHANGE = "aaa.retry.v1"
DEAD_LETTER_EXCHANGE = "aaa.dead.v1"

async def declare_topology() -> None:
    """Declare durable queues once per worker start; safe to call repeatedly."""
    connection = await aio_pika.connect_robust(getenv("RABBITMQ_URL", "amqp://guest:guest@127.0.0.1/"), timeout=2)
    try:
        channel = await connection.channel()
        events = await channel.declare_exchange(EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True)
        retry = await channel.declare_exchange(RETRY_EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True)
        dead = await channel.declare_exchange(DEAD_LETTER_EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True)
        for name, routing_key in (("aaa.accounting", "aaa.accounting.#"), ("aaa.commands", "aaa.disconnect.#"), ("aaa.coa", "aaa.coa.#")):
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
