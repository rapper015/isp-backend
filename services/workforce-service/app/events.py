"""Workforce event contracts (`workforce.events.v1`) + outbox/inbox helpers."""
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from . import models

CONTEXT = "workforce"

PUBLISHED_TOPOLOGY = {
    "workforce.workorder.created.v1": "A field work order was created.",
    "workforce.workorder.assigned.v1": "A work order was assigned to a technician.",
    "workforce.workorder.dispatched.v1": "A technician was dispatched.",
    "workforce.workorder.transitioned.v1": "A work order changed state.",
    "workforce.workorder.completed.v1": "A work order was completed with proof.",
    "workforce.workorder.escalated.v1": "A work order was escalated.",
    "workforce.technician.location.updated.v1": "A technician reported GPS location.",
    "workforce.feedback.submitted.v1": "Customer feedback was submitted.",
    "workforce.inventory.issued.v1": "A device was issued to a technician.",
    "workforce.inventory.synced.v1": "Field inventory was reconciled with the warehouse.",
    "workforce.sla.breached.v1": "A field SLA deadline was missed.",
}

CONSUMED_TOPOLOGY = {
    "crm.ticket.created.v1": "Open a work order from a support ticket.",
    "oss.asset.allocated.v1": "Sync issued asset custody.",
    "oss.maintenance.scheduled.v1": "Create a preventive maintenance work order.",
    "nms.incident.declared.v1": "Create an emergency repair work order.",
}

ALL_PUBLISHED = sorted(PUBLISHED_TOPOLOGY)


def publish(session: Session, event_type: str, aggregate_type: str,
            aggregate_id: str | uuid.UUID, payload: dict, tenant_id=None) -> models.Outbox:
    row = models.Outbox(event_type=event_type, aggregate_type=aggregate_type,
                        aggregate_id=str(aggregate_id), tenant_id=tenant_id, payload=payload)
    session.add(row)
    session.flush()
    return row


def consume_once(session: Session, message_id: str, event_type: str,
                 payload: dict, tenant_id=None) -> bool:
    if session.query(models.Inbox).filter(models.Inbox.message_id == message_id).first():
        return False
    session.add(models.Inbox(message_id=message_id, event_type=event_type,
                             tenant_id=tenant_id, payload=payload))
    session.flush()
    return True


def envelope(event_type: str, payload: dict, source: str = CONTEXT) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "type": event_type,
        "source": source,
        "specversion": "1.0",
        "time": datetime.now(timezone.utc).isoformat(),
        "data": payload,
    }
