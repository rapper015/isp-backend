"""SIEM scheduled tasks: retention sweep, escalation sweep, outbox delivery.

These run from worker_runner.py on a poll loop (features 405, 406, 1472)."""
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from . import events, models
from .context import TenantContext
from .enums import DataClass, RetentionAction
from .services import PolicyService, RetentionService

PLATFORM = TenantContext(user_id="system:worker", role="PLATFORM_ADMIN",
                         permissions={"*"}, scope_kind="PLATFORM_AGGREGATE",
                         is_platform_aggregate=True)


def sweep_retention(session: Session) -> dict:
    """Apply retention across all tenants (features 404-406)."""
    tenant_ids = [r[0] for r in session.query(models.SecurityEvent.tenant_id).distinct().all()]
    total = {"archived": 0, "purged": 0, "checked": 0}
    for tid in tenant_ids:
        ctx = TenantContext(user_id="system:retention", role="PLATFORM_ADMIN",
                            tenant_id=tid, permissions={"*"}, is_platform_aggregate=False)
        try:
            res = RetentionService.apply(session, ctx)
            total["archived"] += res["archived"]
            total["purged"] += res["purged"]
            total["checked"] += res["checked"]
        except PermissionError:
            continue
    return total


def sweep_escalations(session: Session) -> list[dict]:
    """Auto-escalate high/critical severity open cases (feature 1472)."""
    escalated = []
    threshold = datetime.now(timezone.utc) - timedelta(hours=4)
    cases = session.query(models.SecurityCase).filter(
        models.SecurityCase.severity.in_(["HIGH", "CRITICAL"]),
        models.SecurityCase.escalated.is_(False),
        models.SecurityCase.opened_at < threshold).all()
    for c in cases:
        ctx = TenantContext(user_id="system:escalation", role="SECURITY_OPS",
                            tenant_id=c.tenant_id, permissions={"*"},
                            is_platform_aggregate=False)
        c.escalated = True
        session.add(models.CaseEvent(case_id=c.id, tenant_id=c.tenant_id,
                                     from_state=c.status, to_state=c.status,
                                     transition="ESCALATE",
                                     note="Auto-escalated: severity + age threshold",
                                     actor="system:escalation"))
        events.publish(session, "siem.case.escalated.v1", "SecurityCase", c.id,
                       {"case_id": str(c.id), "severity": c.severity},
                       tenant_id=c.tenant_id)
        escalated.append({"case_id": str(c.id), "ref_id": c.ref_id})
    if escalated:
        session.commit()
    return escalated


def rescan_violations(session: Session) -> dict:
    """Continuous compliance scan over recent events (features 426, 450, 1371)."""
    matched = 0
    tenant_ids = [r[0] for r in session.query(models.SecurityEvent.tenant_id).distinct().all()]
    for tid in tenant_ids:
        ctx = TenantContext(user_id="system:scan", role="SECURITY_OPS",
                            tenant_id=tid, permissions={"*"}, is_platform_aggregate=False)
        events_rows = session.query(models.SecurityEvent).filter(
            models.SecurityEvent.tenant_id == tid).order_by(
            models.SecurityEvent.received_at.desc()).limit(100).all()
        for e in events_rows:
            matched += len(PolicyService.evaluate(session, ctx, e))
    session.commit()
    return {"violations_matched": matched}


def deliver_outbox(session: Session, broker=None) -> int:
    """Transactional outbox → broker (stub transport for hermetic operation)."""
    rows = session.query(models.Outbox).filter(models.Outbox.published_at.is_(None)) \
        .order_by(models.Outbox.created_at).limit(500).all()
    delivered = 0
    for r in rows:
        if broker is not None:
            broker.publish(events.envelope(r.event_type, r.payload), r.aggregate_type, r.aggregate_id)
        r.published_at = datetime.now(timezone.utc)
        delivered += 1
    if delivered:
        session.commit()
    return delivered
