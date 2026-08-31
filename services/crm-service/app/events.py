"""Reliable RabbitMQ publication from the CRM transactional outbox, and
inbox-deduplicated consumers. Mirrors the AAA service conventions."""
import asyncio
import json
from datetime import datetime, timezone
from os import getenv
from uuid import UUID

import aio_pika
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ConsumerInbox, OutboxEvent

EXCHANGE = "crm.events.v1"
RETRY_EXCHANGE = "crm.retry.v1"
DEAD_LETTER_EXCHANGE = "crm.dead.v1"

# Versioned CRM events published from the outbox.
CRM_EVENTS = (
    "crm.lead.created.v1",
    "crm.lead.assigned.v1",
    "crm.lead.stage_changed.v1",
    "crm.lead.feasibility_requested.v1",
    "crm.lead.converted.v1",
    "crm.customer.created.v1",
    "crm.customer.updated.v1",
    "crm.customer.merged.v1",
    "crm.customer.contact_verified.v1",
    "crm.customer.address_changed.v1",
    "crm.kyc.submitted.v1",
    "crm.kyc.verified.v1",
    "crm.kyc.rejected.v1",
    "crm.caf.approved.v1",
    "crm.customer.lifecycle_changed.v1",
    "crm.customer.risk_changed.v1",
    "crm.followup.due.v1",
    "crm.followup.overdue.v1",
    "crm.partner.created.v1",
    "crm.partner.performance.updated.v1",
    "crm.partner.sla_evaluated.v1",
    "crm.federation.linked.v1",
    "crm.ticket.sla_breached.v1",
    "crm.ticket.escalated.v1",
    "crm.suggestion.generated.v1",
    "crm.regulatory.tracked.v1",
    "crm.kb.feedback.captured.v1",
    "crm.recovery.triggered.v1",
    "crm.loyalty.score.calculated.v1",
)


async def declare_topology() -> None:
    connection = await aio_pika.connect_robust(getenv("RABBITMQ_URL", "amqp://guest:guest@127.0.0.1/"), timeout=2)
    try:
        channel = await connection.channel()
        events = await channel.declare_exchange(EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True)
        retry = await channel.declare_exchange(RETRY_EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True)
        dead = await channel.declare_exchange(DEAD_LETTER_EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True)
        for name, routing_key in (("crm.leads", "crm.lead.#"), ("crm.customers", "crm.customer.#"), ("crm.followups", "crm.followup.#")):
            queue = await channel.declare_queue(name, durable=True, arguments={"x-dead-letter-exchange": RETRY_EXCHANGE})
            await queue.bind(events, routing_key)
            retry_queue = await channel.declare_queue(f"{name}.retry", durable=True, arguments={"x-message-ttl": 30000, "x-dead-letter-exchange": EXCHANGE})
            await retry_queue.bind(retry, routing_key)
            dead_queue = await channel.declare_queue(f"{name}.dead", durable=True)
            await dead_queue.bind(dead, routing_key)
    finally:
        await connection.close()


def envelope(event: OutboxEvent) -> dict:
    return {
        "event_id": str(event.id),
        "event_type": event.event_type,
        "schema_version": 1,
        "occurred_at": event.occurred_at.isoformat(),
        "published_at": datetime.now(timezone.utc).isoformat(),
        "tenant_id": str(event.tenant_id) if event.tenant_id else None,
        "correlation_id": event.correlation_id,
        "causation_id": None,
        "idempotency_key": event.idempotency_key,
        "producer": "crm-service",
        "trace_context": {},
        "payload": event.payload,
    }


async def _publish(messages: list[OutboxEvent]) -> None:
    connection = await aio_pika.connect_robust(getenv("RABBITMQ_URL", "amqp://guest:guest@127.0.0.1/"), timeout=2)
    try:
        channel = await connection.channel(publisher_confirms=True)
        exchange = await channel.declare_exchange(EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True)
        for item in messages:
            await exchange.publish(
                aio_pika.Message(body=json.dumps(envelope(item), default=str).encode(), delivery_mode=aio_pika.DeliveryMode.PERSISTENT, content_type="application/json", message_id=str(item.id)),
                routing_key=item.event_type,
            )
    finally:
        await connection.close()


def publish_outbox(session: Session, limit: int = 100) -> int:
    pending = list(session.scalars(select(OutboxEvent).where(OutboxEvent.published_at.is_(None)).order_by(OutboxEvent.occurred_at).limit(limit)))
    if not pending:
        return 0
    try:
        asyncio.run(_publish(pending))
    except Exception:
        for item in pending:
            item.attempts += 1
        session.commit()
        return 0
    now = datetime.now(timezone.utc)
    for item in pending:
        item.published_at = now
        item.attempts += 1
    session.commit()
    return len(pending)


async def consume_once(processor, queue_name: str, prefix: str, timeout: float = 0.25) -> str | None:
    """Consume one durable CRM event with inbox deduplication."""
    from .database import SessionLocal
    connection = await aio_pika.connect_robust(getenv("RABBITMQ_URL", "amqp://guest:guest@127.0.0.1/"), timeout=2)
    try:
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=1)
        queue = await channel.declare_queue(queue_name, durable=True, arguments={"x-dead-letter-exchange": RETRY_EXCHANGE})
        message = await queue.get(timeout=timeout, fail=False)
        if message is None:
            return None
        async with message.process(requeue=False):
            try:
                payload = json.loads(message.body)
                event_id = UUID(payload["event_id"])
                if not payload.get("event_type", "").startswith(prefix):
                    return "ignored"
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                return "invalid"
            session = SessionLocal()
            try:
                consumer = f"crm-worker:{queue_name}"
                if session.get(ConsumerInbox, {"event_id": event_id, "consumer": consumer}):
                    return "duplicate"
                session.add(ConsumerInbox(event_id=event_id, consumer=consumer))
                session.commit()
                return processor(session, payload) or "missing"
            finally:
                session.close()
    finally:
        await connection.close()
