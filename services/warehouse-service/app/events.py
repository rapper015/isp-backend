"""Outbox + event topology for the Warehouse service."""
import json
import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid as SqlUuid

from .database import Base

EXCHANGE = "warehouse.events.v1"

PUBLISHED_TOPOLOGY = {
    "warehouse.kpi.updated.v1",
    "warehouse.kpi.set.v1",
    "warehouse.revenue_trend.recorded.v1",
    "warehouse.revenue.analyzed.v1",
    "warehouse.profitability.recorded.v1",
    "warehouse.profit.analyzed.v1",
    "warehouse.cluster.scaled.v1",
    "warehouse.ecosystem.recorded.v1",
    "warehouse.ecosystem.analyzed.v1",
}


class Outbox(Base):
    __tablename__ = "wh_outbox"
    id: Mapped[uuid.UUID] = mapped_column(SqlUuid, primary_key=True, default=uuid.uuid4)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(SqlUuid, nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


def outbox(session, event_type: str, tenant_id, payload: dict) -> Outbox:
    if event_type not in PUBLISHED_TOPOLOGY:
        raise ValueError(f"Unknown event type: {event_type}")
    row = Outbox(
        event_type=event_type,
        tenant_id=uuid.UUID(str(tenant_id)) if not isinstance(tenant_id, uuid.UUID) else tenant_id,
        payload=json.dumps(payload, default=str),
        created_at=datetime.utcnow(),
    )
    session.add(row)
    return row
