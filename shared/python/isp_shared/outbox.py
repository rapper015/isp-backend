"""At-least-once, confirmed outbox delivery. Consumers must deduplicate event_id."""
import asyncio
import json
import logging
import os
from datetime import datetime, timezone

from sqlalchemy import select

log = logging.getLogger("isp.outbox")


def envelope(row, producer):
    occurred = getattr(row, "occurred_at", None) or getattr(row, "created_at", None)
    return {"event_id": str(row.id), "event_type": row.event_type, "schema_version": 1,
            "producer": producer, "tenant_id": str(row.tenant_id) if row.tenant_id else None,
            "correlation_id": getattr(row, "correlation_id", None),
            "idempotency_key": getattr(row, "idempotency_key", None) or str(row.id),
            "occurred_at": occurred.isoformat() if occurred else None, "payload": row.payload}


async def _publish(row, exchange_name):
    import aio_pika
    from pamqp.commands import Basic
    url = os.getenv("RABBITMQ_URL", "")
    if not url:
        raise RuntimeError("RabbitMQ publication is not configured")
    timeout = float(os.getenv("OUTBOX_PUBLISH_TIMEOUT_SECONDS", "5"))
    connection = await aio_pika.connect(url, timeout=timeout)
    try:
        channel = await connection.channel(publisher_confirms=True, on_return_raises=True)
        exchange = await channel.declare_exchange(exchange_name, aio_pika.ExchangeType.TOPIC, durable=True, timeout=timeout)
        confirmed = await exchange.publish(aio_pika.Message(
            body=json.dumps(envelope(row, exchange_name.split(".events")[0]), default=str).encode(),
            message_id=str(row.id), correlation_id=getattr(row, "correlation_id", None),
            content_type="application/json", delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        ), routing_key=row.event_type, mandatory=True, timeout=timeout)
        if not isinstance(confirmed, Basic.Ack):
            raise RuntimeError("Broker did not acknowledge event")
        return True
    finally:
        await connection.close()


def publish(row, exchange_name):
    return asyncio.run(_publish(row, exchange_name))


def flush(session, model, exchange_name, *, limit=100, publisher=None, commit=True):
    """Lock the batch until confirmation is committed; never mark no-op delivery.

    A crash after broker ACK and before commit can redeliver the same event ID.
    Never reset historical published_at values without an explicit replay plan.
    """
    order_column = getattr(model, "occurred_at", None)
    if order_column is None:
        order_column = getattr(model, "created_at", model.id)
    rows = list(session.scalars(select(model).where(model.published_at.is_(None))
        .order_by(order_column).limit(limit).with_for_update(skip_locked=True)))
    delivered = []
    for row in rows:
        if hasattr(row, "attempts"):
            row.attempts += 1
        try:
            ack = publisher(row) if publisher else publish(row, exchange_name)
            if ack is not True:
                raise RuntimeError("Publisher must confirm delivery")
            row.published_at = datetime.now(timezone.utc)
            delivered.append(row.event_type)
        except Exception:
            log.exception("outbox.delivery_failed", extra={"operation": row.event_type,
                "entity_id": str(row.id), "correlation_id": getattr(row, "correlation_id", None)})
            break  # Avoid hammering an unavailable broker for every pending row.
    if commit:
        session.commit()
    else:
        session.flush()
    return delivered
