"""NMS event contracts + transactional outbox."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.orm import Session

from .database import Base

EXCHANGE = "nms.events.v1"


class OutboxEvent(Base):
    __tablename__ = "nms_outbox"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    event_type: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(index=True, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


PUBLISHED_TOPOLOGY = {
    "nms.escalation.policy_created.v1",
    "nms.escalation.triggered.v1",
    "nms.config.diff_detected.v1",
    "nms.config.diff.generated.v1",
    "nms.approval.sla_overdue.v1",
    "nms.approval.tracked.v1",
    "nms.cache.strategy_updated.v1",
    "nms.cache.optimized.v1",
    "nms.degradation.rule_applied.v1",
    "nms.degradation.applied.v1",
    "nms.queue.saturation_protected.v1",
    "nms.queue.protection.applied.v1",
}


def outbox(session: Session, event_type: str, tenant_id, payload: dict) -> None:
    if event_type not in PUBLISHED_TOPOLOGY:
        raise ValueError(f"unknown NMS event type {event_type!r}")
    session.add(OutboxEvent(event_type=event_type, tenant_id=tenant_id, payload=payload))


def publish_pending(session: Session, limit: int = 100) -> int:
    rows = list(session.query(OutboxEvent).filter(OutboxEvent.published_at.is_(None))
                .order_by(OutboxEvent.occurred_at).limit(limit).all())
    for r in rows:
        r.published_at = datetime.now(timezone.utc)
    if rows:
        session.commit()
    return len(rows)
