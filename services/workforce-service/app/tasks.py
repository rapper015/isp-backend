"""Workforce scheduled tasks: SLA evaluation, KPI compute, outbox delivery,
preventive-maintenance scheduling (features 347, 346, 1117)."""
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from . import events, models
from .context import TenantContext
from .services import KpiService, SlaService

PLATFORM = TenantContext(user_id="system:worker", role="PLATFORM_ADMIN",
                         permissions={"*"}, scope_kind="PLATFORM_AGGREGATE",
                         is_platform_aggregate=True)


def _tenant_ctx(tid) -> TenantContext:
    return TenantContext(user_id="system:worker", role="FIELD_MANAGER",
                         tenant_id=tid, permissions={"*"}, is_platform_aggregate=False)


def sweep_sla(session: Session) -> dict:
    tenant_ids = [r[0] for r in session.query(models.FieldSLA.tenant_id).distinct().all()]
    total = {"breached": 0, "on_time": 0, "checked": 0}
    for tid in tenant_ids:
        res = SlaService.evaluate(session, _tenant_ctx(tid))
        total["breached"] += res["breached"]
        total["on_time"] += res["on_time"]
        total["checked"] += res["checked"]
    return total


def compute_kpis(session: Session) -> int:
    techs = session.query(models.Technician.id, models.Technician.tenant_id).distinct().all()
    count = 0
    for tech_id, tid in techs:
        KpiService.recompute(session, _tenant_ctx(tid), tech_id, period="DAY")
        count += 1
    return count


def schedule_preventive_maintenance(session: Session) -> list[dict]:
    """Create PM work orders for assets whose last maintenance is overdue (1117)."""
    created = []
    # Simple policy: a monthly preventive maintenance work order per tenant.
    tenant_ids = [r[0] for r in session.query(models.Technician.tenant_id).distinct().all()]
    for tid in tenant_ids:
        now = datetime.now(timezone.utc)
        existing = session.query(models.WorkOrder).filter(
            models.WorkOrder.tenant_id == tid,
            models.WorkOrder.type == "PREVENTIVE_MAINTENANCE",
            models.WorkOrder.created_at >= now - timedelta(days=30)).count()
        if existing == 0:
            year = now.year
            n = (session.query(models.WorkOrder).filter(
                models.WorkOrder.tenant_id == tid).count()) + 1
            wo = models.WorkOrder(tenant_id=tid, ref_id=f"WO-PM-{year}-{n:05d}",
                                  title="Routine preventive maintenance",
                                  type="PREVENTIVE_MAINTENANCE", status="CREATED",
                                  priority="LOW")
            session.add(wo)
            session.flush()
            events.publish(session, "workforce.workorder.created.v1", "WorkOrder", wo.id,
                           {"work_order_id": str(wo.id), "type": "PREVENTIVE_MAINTENANCE"},
                           tenant_id=tid)
            created.append({"work_order_id": str(wo.id), "tenant_id": str(tid)})
    if created:
        session.commit()
    return created


def deliver_outbox(session: Session, broker=None) -> int:
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
