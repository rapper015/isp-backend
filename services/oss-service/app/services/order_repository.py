"""Order aggregate repository: load by events and append events with
optimistic concurrency (unique (order_id, aggregate_version))."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import Order, OrderEvent


class OrderNotFound(Exception):
    pass


class ConcurrencyConflict(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


class OrderRepository:
    def __init__(self, session: Session):
        self.session = session

    def load(self, order_id: uuid.UUID) -> Order:
        order = self.session.get(Order, order_id)
        if order is None:
            raise OrderNotFound(order_id)
        return order

    def get_by_number(self, tenant_id: uuid.UUID, order_number: str) -> Order | None:
        return self.session.scalar(select(Order).where(Order.tenant_id == tenant_id, Order.order_number == order_number))

    def events(self, order_id: uuid.UUID) -> list[OrderEvent]:
        return list(
            self.session.scalars(
                select(OrderEvent).where(OrderEvent.order_id == order_id).order_by(OrderEvent.aggregate_version)
            )
        )

    def append(
        self,
        order: Order,
        event_type: str,
        payload: dict,
        actor_type: str | None,
        actor_id: str | None,
        correlation_id: str | None,
        causation_id: str | None = None,
        idempotency_key: str | None = None,
        expected_version: int | None = None,
    ) -> OrderEvent:
        expected = order.aggregate_version if expected_version is None else expected_version
        order_id = order.id  # captured before flush; object expires on failure
        event = OrderEvent(
            tenant_id=order.tenant_id,
            order_id=order.id,
            aggregate_version=expected + 1,
            event_type=event_type,
            actor_type=actor_type,
            actor_id=actor_id,
            correlation_id=correlation_id,
            causation_id=causation_id,
            idempotency_key=idempotency_key,
            payload=payload,
            event_metadata={"recorded_at": _now().isoformat()},
        )
        self.session.add(event)
        order.aggregate_version = expected + 1
        try:
            self.session.flush()
        except IntegrityError as error:
            raise ConcurrencyConflict(f"order {order_id} concurrently modified") from error
        return event
