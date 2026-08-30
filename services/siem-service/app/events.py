"""SIEM event contracts: published/consumed topology + outbox/inbox helpers.

Events use the `<context>.<aggregate>.<action>.v1` convention (ADR-010 D6)."""
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from . import models

CONTEXT = "siem"

PUBLISHED_TOPOLOGY = {
    "siem.security_event.ingested.v1": "A security event was accepted into the tamper-evident log.",
    "siem.policy.violation_detected.v1": "A compliance policy rule matched an event.",
    "siem.case.created.v1": "A security case was opened.",
    "siem.case.transitioned.v1": "A security case changed state.",
    "siem.case.escalated.v1": "A case was escalated per the escalation matrix.",
    "siem.data_request.completed.v1": "A data access/erasure request was fulfilled.",
    "siem.consent.updated.v1": "Subscriber consent was granted/revoked.",
    "siem.retention.purged.v1": "Records were archived or purged per retention policy.",
    "siem.breach.notified.v1": "A breach notification was issued.",
    "siem.vulnerability.ingested.v1": "A vulnerability scan finding was recorded.",
}

CONSUMED_TOPOLOGY = {
    "tenancy.tenant.created.v1": "Provision a tenant SIEM workspace.",
    "crm.subscriber.created.v1": "Initialize consent baseline for a subscriber.",
    "aiops.anomaly.detected.v1": "Correlate anomalies into security cases.",
    "assurance.incident.declared.v1": "Open a linked security case for incidents.",
}

# Events published by this service are also consumable by siblings.
ALL_PUBLISHED = sorted(PUBLISHED_TOPOLOGY)


def publish(session: Session, event_type: str, aggregate_type: str,
            aggregate_id: str | uuid.UUID, payload: dict, tenant_id=None) -> models.Outbox:
    """Transactional outbox write. Delivered by the worker to the broker."""
    row = models.Outbox(
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=str(aggregate_id),
        tenant_id=tenant_id,
        payload=payload,
    )
    session.add(row)
    session.flush()
    return row


def consume_once(session: Session, message_id: str, event_type: str,
                 payload: dict, tenant_id=None) -> bool:
    """Idempotent inbox consume. Returns True if this message was newly applied."""
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
