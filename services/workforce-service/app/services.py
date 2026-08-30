"""Workforce domain services: work orders, dispatch, inventory, field ops,
SLA/KPI, escalation, shifts, feedback (Master Spec Batch 2)."""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from . import events, models
from .context import TenantContext
from .enums import (EscalationLevel, InventoryStatus, TechnicianStatus,
                    WorkOrderStatus, WorkOrderType, WO_FLOW)
from .routing import record_audit


def _utcnow():
    return datetime.now(timezone.utc)


class WorkOrderService:
    @staticmethod
    def create(session: Session, ctx: TenantContext, data: dict) -> models.WorkOrder:
        tenant_id = ctx.require_tenant()
        year = _utcnow().year
        n = (session.query(func.count(models.WorkOrder.id)).filter(
            models.WorkOrder.tenant_id == tenant_id).scalar() or 0) + 1
        ref = f"WO-{year}-{n:08d}"
        sla_minutes = data.pop("sla_minutes", None)
        wo = models.WorkOrder(tenant_id=tenant_id, ref_id=ref, status="CREATED", **data)
        session.add(wo)
        session.flush()
        if sla_minutes:
            SlaService.attach(session, tenant_id, wo.id, sla_minutes)
        session.commit()
        events.publish(session, "workforce.workorder.created.v1", "WorkOrder", wo.id,
                       {"work_order_id": str(wo.id), "ref_id": ref, "type": wo.type,
                        "status": wo.status, "priority": wo.priority}, tenant_id=tenant_id)
        session.commit()
        record_audit(session, ctx, "workorder.create", "WorkOrder", str(wo.id))
        session.commit()
        return wo

    @staticmethod
    def transition(session: Session, ctx: TenantContext, wo_id: uuid.UUID,
                   transition: str, note: str | None = None) -> models.WorkOrder:
        wo = session.query(models.WorkOrder).filter(
            models.WorkOrder.id == wo_id,
            models.WorkOrder.tenant_id == ctx.require_tenant()).first()
        if not wo:
            raise KeyError("Work order not found")
        allowed = WO_FLOW.get(WorkOrderStatus(wo.status), set())
        if transition not in allowed:
            raise ValueError(f"Invalid transition {transition} from {wo.status}")
        wo.status = transition
        if transition == "COMPLETED":
            wo.completed_at = _utcnow()
        session.commit()
        events.publish(session, "workforce.workorder.transitioned.v1", "WorkOrder", wo.id,
                       {"work_order_id": str(wo.id), "to": transition,
                        "note": note}, tenant_id=wo.tenant_id)
        if transition == "COMPLETED":
            events.publish(session, "workforce.workorder.completed.v1", "WorkOrder", wo.id,
                           {"work_order_id": str(wo.id), "ref_id": wo.ref_id},
                           tenant_id=wo.tenant_id)
        session.commit()
        record_audit(session, ctx, "workorder.transition", "WorkOrder", str(wo.id),
                     detail={"to": transition})
        session.commit()
        return wo

    @staticmethod
    def assign(session: Session, ctx: TenantContext, wo_id: uuid.UUID,
               tech_id: uuid.UUID, notes: str | None = None,
               scheduled_start=None, scheduled_end=None) -> models.WorkOrder:
        tenant_id = ctx.require_tenant()
        wo = session.query(models.WorkOrder).filter(
            models.WorkOrder.id == wo_id, models.WorkOrder.tenant_id == tenant_id).first()
        if not wo:
            raise KeyError("Work order not found")
        tech = session.query(models.Technician).filter(
            models.Technician.id == tech_id, models.Technician.tenant_id == tenant_id).first()
        if not tech:
            raise KeyError("Technician not found")
        session.add(models.Assignment(tenant_id=tenant_id, work_order_id=wo.id,
                                      technician_id=tech.id, status="ASSIGNED", notes=notes))
        wo.technician_id = tech.id
        if wo.status in ("CREATED", "SCHEDULING"):
            wo.status = "ASSIGNED"
        if scheduled_start and scheduled_end:
            session.add(models.Appointment(tenant_id=tenant_id, work_order_id=wo.id,
                                           scheduled_start=scheduled_start,
                                           scheduled_end=scheduled_end,
                                           status="CONFIRMATION_PENDING"))
        session.commit()
        events.publish(session, "workforce.workorder.assigned.v1", "WorkOrder", wo.id,
                       {"work_order_id": str(wo.id), "technician_id": str(tech.id)},
                       tenant_id=tenant_id)
        session.commit()
        record_audit(session, ctx, "workorder.assign", "WorkOrder", str(wo.id),
                     detail={"technician_id": str(tech.id)})
        session.commit()
        return wo

    @staticmethod
    def dispatch(session: Session, ctx: TenantContext, wo_id: uuid.UUID,
                 notes: str | None = None) -> models.WorkOrder:
        wo = session.query(models.WorkOrder).filter(
            models.WorkOrder.id == wo_id,
            models.WorkOrder.tenant_id == ctx.require_tenant()).first()
        if not wo:
            raise KeyError("Work order not found")
        if wo.status not in ("ASSIGNED", "SCHEDULING"):
            raise ValueError(f"Cannot dispatch from {wo.status}")
        if not wo.technician_id:
            raise ValueError("No technician assigned")
        tech = session.query(models.Technician).filter(
            models.Technician.id == wo.technician_id).first()
        if tech:
            tech.status = "BUSY"
        wo.status = "DISPATCHED"
        session.commit()
        events.publish(session, "workforce.workorder.dispatched.v1", "WorkOrder", wo.id,
                       {"work_order_id": str(wo.id), "technician_id": str(wo.technician_id),
                        "notes": notes}, tenant_id=wo.tenant_id)
        session.commit()
        record_audit(session, ctx, "workorder.dispatch", "WorkOrder", str(wo.id))
        session.commit()
        return wo

    @staticmethod
    def complete(session: Session, ctx: TenantContext, wo_id: uuid.UUID,
                 note: str | None = None) -> models.WorkOrder:
        wo = WorkOrderService.transition(session, ctx, wo_id, "COMPLETED", note)
        if wo.technician_id:
            tech = session.query(models.Technician).filter(
                models.Technician.id == wo.technician_id).first()
            if tech:
                tech.status = "AVAILABLE"
        assignment = session.query(models.Assignment).filter(
            models.Assignment.work_order_id == wo.id).order_by(
            models.Assignment.assigned_at.desc()).first()
        if assignment:
            assignment.status = "COMPLETED"
        session.commit()
        return wo


class TechnicianService:
    @staticmethod
    def create(session: Session, ctx: TenantContext, data: dict) -> models.Technician:
        t = models.Technician(tenant_id=ctx.require_tenant(), status="AVAILABLE", **data)
        session.add(t)
        session.commit()
        record_audit(session, ctx, "technician.create", "Technician", str(t.id))
        session.commit()
        return t

    @staticmethod
    def set_status(session: Session, ctx: TenantContext, tech_id: uuid.UUID,
                   status: str) -> models.Technician:
        t = session.query(models.Technician).filter(
            models.Technician.id == tech_id,
            models.Technician.tenant_id == ctx.require_tenant()).first()
        if not t:
            raise KeyError("Technician not found")
        if status not in {s.value for s in TechnicianStatus}:
            raise ValueError("Invalid technician status")
        t.status = status
        session.commit()
        record_audit(session, ctx, "technician.status", "Technician", str(tech_id),
                     detail={"status": status})
        session.commit()
        return t

    @staticmethod
    def update_location(session: Session, ctx: TenantContext, tech_id: uuid.UUID,
                        lat: float, lon: float) -> models.Technician:
        q = session.query(models.Technician).filter(models.Technician.id == tech_id)
        if not ctx.is_platform_aggregate:
            q = q.filter(models.Technician.tenant_id == ctx.require_tenant())
        t = q.first()
        if not t:
            raise KeyError("Technician not found")
        t.last_lat, t.last_lon, t.location_updated_at = lat, lon, _utcnow()
        session.commit()
        events.publish(session, "workforce.technician.location.updated.v1", "Technician",
                       t.id, {"technician_id": str(t.id), "lat": lat, "lon": lon},
                       tenant_id=t.tenant_id)
        session.commit()
        return t


class DispatchService:
    @staticmethod
    def suggest(session: Session, ctx: TenantContext, wo_id: uuid.UUID,
                skills: list[str] | None = None) -> list[models.Technician]:
        tenant_id = ctx.require_tenant()
        q = session.query(models.Technician).filter(
            models.Technician.tenant_id == tenant_id,
            models.Technician.status == "AVAILABLE")
        candidates = [t for t in q.all()
                      if not skills or (set(skills) & set(t.skills or []))]
        return sorted(candidates, key=lambda t: (-t.rating, t.joined_at))


class InventoryService:
    @staticmethod
    def add_item(session: Session, ctx: TenantContext, data: dict) -> models.InventoryItem:
        it = models.InventoryItem(tenant_id=ctx.require_tenant(), status="IN_STOCK", **data)
        session.add(it)
        session.commit()
        return it

    @staticmethod
    def issue(session: Session, ctx: TenantContext, item_id: uuid.UUID,
              wo_id: uuid.UUID, tech_id: uuid.UUID | None = None) -> models.InventoryItem:
        it = session.query(models.InventoryItem).filter(
            models.InventoryItem.id == item_id,
            models.InventoryItem.tenant_id == ctx.require_tenant()).first()
        if not it:
            raise KeyError("Inventory item not found")
        if it.status != "IN_STOCK":
            raise ValueError(f"Item is {it.status}, cannot issue")
        it.status = "ISSUED"
        it.work_order_id = wo_id
        it.assigned_technician_id = tech_id
        it.issued_at = _utcnow()
        session.commit()
        events.publish(session, "workforce.inventory.issued.v1", "InventoryItem", it.id,
                       {"item_id": str(it.id), "work_order_id": str(wo_id)},
                       tenant_id=it.tenant_id)
        session.commit()
        return it

    @staticmethod
    def return_item(session: Session, ctx: TenantContext, item_id: uuid.UUID) -> models.InventoryItem:
        it = session.query(models.InventoryItem).filter(
            models.InventoryItem.id == item_id,
            models.InventoryItem.tenant_id == ctx.require_tenant()).first()
        if not it:
            raise KeyError("Inventory item not found")
        it.status = "RETURNED"
        it.returned_at = _utcnow()
        it.work_order_id = None
        it.assigned_technician_id = None
        session.commit()
        return it

    @staticmethod
    def sync_stock(session: Session, ctx: TenantContext,
                   stock: list[dict]) -> dict:
        """Inventory Sync (339): reconcile field inventory with the warehouse."""
        tenant_id = ctx.require_tenant()
        reconciled = 0
        for entry in stock:
            serial = entry.get("serial_number") or entry.get("mac_address")
            if not serial:
                continue
            item = session.query(models.InventoryItem).filter(
                models.InventoryItem.tenant_id == tenant_id,
                (models.InventoryItem.serial_number == serial)
                | (models.InventoryItem.mac_address == serial)).first()
            status = entry.get("status")
            if item and status and status in {s.value for s in InventoryStatus}:
                item.status = status
                reconciled += 1
        session.commit()
        events.publish(session, "workforce.inventory.synced.v1", "InventoryItem",
                       str(tenant_id), {"reconciled": reconciled}, tenant_id=tenant_id)
        session.commit()
        return {"reconciled": reconciled}

    @staticmethod
    def add_consumable(session: Session, ctx: TenantContext, data: dict) -> models.Consumable:
        tenant_id = ctx.require_tenant()
        row = session.query(models.Consumable).filter(
            models.Consumable.tenant_id == tenant_id,
            models.Consumable.sku == data["sku"]).first()
        if row:
            row.quantity += data.get("quantity", 0)
            row.low_threshold = data.get("low_threshold", row.low_threshold)
        else:
            row = models.Consumable(tenant_id=tenant_id, **data)
            session.add(row)
        session.commit()
        return row

    @staticmethod
    def consume(session: Session, ctx: TenantContext, wo_id: uuid.UUID, sku: str,
                quantity: int) -> models.Consumption:
        tenant_id = ctx.require_tenant()
        c = session.query(models.Consumable).filter(
            models.Consumable.tenant_id == tenant_id,
            models.Consumable.sku == sku).first()
        if not c:
            raise KeyError("Consumable not found")
        if c.quantity < quantity:
            raise ValueError("INSUFFICIENT_STOCK")
        c.quantity -= quantity
        row = models.Consumption(tenant_id=tenant_id, work_order_id=wo_id,
                                 consumable_id=c.id, quantity=quantity)
        session.add(row)
        session.commit()
        return row


class ChecklistService:
    @staticmethod
    def set_template(session: Session, ctx: TenantContext, work_order_type: str,
                     items: list[str]) -> models.ChecklistTemplate:
        tenant_id = ctx.require_tenant()
        t = session.query(models.ChecklistTemplate).filter(
            models.ChecklistTemplate.tenant_id == tenant_id,
            models.ChecklistTemplate.work_order_type == work_order_type).first()
        if t:
            t.items = items
        else:
            t = models.ChecklistTemplate(tenant_id=tenant_id,
                                         work_order_type=work_order_type, items=items)
            session.add(t)
        session.commit()
        return t

    @staticmethod
    def validate(session: Session, ctx: TenantContext, wo_id: uuid.UUID,
                 completed: list[str]) -> tuple[bool, list[str]]:
        wo = session.query(models.WorkOrder).filter(
            models.WorkOrder.id == wo_id,
            models.WorkOrder.tenant_id == ctx.require_tenant()).first()
        if not wo:
            raise KeyError("Work order not found")
        template = session.query(models.ChecklistTemplate).filter(
            models.ChecklistTemplate.tenant_id == wo.tenant_id,
            models.ChecklistTemplate.work_order_type == wo.type).first()
        required = (template.items if template else []) or []
        missing = [i for i in required if i not in completed]
        return (not missing, missing)


class FieldOpsService:
    @staticmethod
    def site_check(session: Session, ctx: TenantContext, wo_id: uuid.UUID,
                   kind: str, passed: bool, details: dict) -> models.SiteCheck:
        c = models.SiteCheck(tenant_id=ctx.require_tenant(), work_order_id=wo_id,
                             kind=kind, passed=passed, details=details,
                             checked_by=ctx.user_id)
        session.add(c)
        session.commit()
        record_audit(session, ctx, "fieldops.site_check", "SiteCheck", str(c.id),
                     detail={"kind": kind, "passed": passed})
        session.commit()
        return c

    @staticmethod
    def handover(session: Session, ctx: TenantContext, wo_id: uuid.UUID,
                 accepted_by: str | None, notes: str | None) -> models.Handover:
        h = models.Handover(tenant_id=ctx.require_tenant(), work_order_id=wo_id,
                            accepted_by=accepted_by, notes=notes)
        session.add(h)
        session.commit()
        record_audit(session, ctx, "fieldops.handover", "Handover", str(h.id))
        session.commit()
        return h


class VisitService:
    @staticmethod
    def record(session: Session, ctx: TenantContext, wo_id: uuid.UUID, tech_id: uuid.UUID,
               visit_type: str, lat: float | None, lon: float | None,
               notes: str | None) -> models.Visit:
        v = models.Visit(tenant_id=ctx.require_tenant(), work_order_id=wo_id,
                         technician_id=tech_id, visit_type=visit_type,
                         lat=lat, lon=lon, notes=notes)
        session.add(v)
        session.commit()
        return v

    @staticmethod
    def add_proof(session: Session, ctx: TenantContext, wo_id: uuid.UUID, kind: str,
                  evidence_key: str, visit_id: uuid.UUID | None = None) -> models.ProofOfWork:
        p = models.ProofOfWork(tenant_id=ctx.require_tenant(), work_order_id=wo_id,
                               kind=kind, evidence_key=evidence_key, visit_id=visit_id,
                               uploaded_by=ctx.user_id)
        session.add(p)
        session.commit()
        return p


class SlaService:
    @staticmethod
    def attach(session: Session, tenant_id, wo_id: uuid.UUID, minutes: int) -> models.FieldSLA:
        deadline = _utcnow() + timedelta(minutes=minutes)
        s = models.FieldSLA(tenant_id=tenant_id, work_order_id=wo_id,
                            sla_minutes=minutes, deadline=deadline)
        session.add(s)
        session.flush()
        wo = session.query(models.WorkOrder).filter(models.WorkOrder.id == wo_id).first()
        if wo:
            wo.sla_deadline = deadline
        return s

    @staticmethod
    def evaluate(session: Session, ctx: TenantContext) -> dict:
        tenant_id = ctx.require_tenant()
        breached = 0
        ok = 0
        rows = session.query(models.FieldSLA).filter(
            models.FieldSLA.tenant_id == tenant_id,
            models.FieldSLA.deadline < _utcnow(),
            models.FieldSLA.checked_at.is_(None)).all()
        for s in rows:
            wo = session.query(models.WorkOrder).filter(
                models.WorkOrder.id == s.work_order_id).first()
            done_at = wo.completed_at if wo and wo.status == "COMPLETED" else None
            if done_at:
                s.actual_minutes = max(0, int((done_at - (s.deadline - timedelta(minutes=s.sla_minutes))).total_seconds() // 60))
                s.breached = s.actual_minutes > s.sla_minutes
                ok += 1
            else:
                s.breached = True
                breached += 1
            s.checked_at = _utcnow()
            if s.breached:
                events.publish(session, "workforce.sla.breached.v1", "FieldSLA", s.id,
                               {"work_order_id": str(s.work_order_id),
                                "deadline": s.deadline.isoformat()}, tenant_id=tenant_id)
        session.commit()
        return {"breached": breached, "on_time": ok, "checked": len(rows)}


class EscalationService:
    @staticmethod
    def create(session: Session, ctx: TenantContext, wo_id: uuid.UUID, level: str,
               reason: str | None) -> models.Escalation:
        e = models.Escalation(tenant_id=ctx.require_tenant(), work_order_id=wo_id,
                              level=level, reason=reason, status="OPEN")
        session.add(e)
        session.commit()
        events.publish(session, "workforce.workorder.escalated.v1", "Escalation", e.id,
                       {"escalation_id": str(e.id), "work_order_id": str(wo_id),
                        "level": level, "reason": reason}, tenant_id=e.tenant_id)
        session.commit()
        record_audit(session, ctx, "escalation.create", "Escalation", str(e.id))
        session.commit()
        return e

    @staticmethod
    def resolve(session: Session, ctx: TenantContext, esc_id: uuid.UUID) -> models.Escalation:
        e = session.query(models.Escalation).filter(
            models.Escalation.id == esc_id,
            models.Escalation.tenant_id == ctx.require_tenant()).first()
        if not e:
            raise KeyError("Escalation not found")
        e.status = "RESOLVED"
        e.resolved_at = _utcnow()
        session.commit()
        return e


class FeedbackService:
    @staticmethod
    def submit(session: Session, ctx: TenantContext, wo_id: uuid.UUID, rating: int,
               comment: str | None) -> models.Feedback:
        f = models.Feedback(tenant_id=ctx.require_tenant(), work_order_id=wo_id,
                            rating=rating, comment=comment)
        session.add(f)
        session.flush()
        tech_id = session.query(models.WorkOrder.technician_id).filter(
            models.WorkOrder.id == wo_id).scalar()
        if tech_id:
            KpiService.recompute(session, ctx, tech_id, period="DAY")
        session.commit()
        events.publish(session, "workforce.feedback.submitted.v1", "Feedback", f.id,
                       {"feedback_id": str(f.id), "work_order_id": str(wo_id),
                        "rating": rating}, tenant_id=f.tenant_id)
        session.commit()
        return f


class ShiftService:
    @staticmethod
    def create(session: Session, ctx: TenantContext, data: dict) -> models.Shift:
        s = models.Shift(tenant_id=ctx.require_tenant(), status="SCHEDULED", **data)
        session.add(s)
        session.commit()
        record_audit(session, ctx, "shift.create", "Shift", str(s.id))
        session.commit()
        return s


class KpiService:
    @staticmethod
    def recompute(session: Session, ctx: TenantContext, tech_id: uuid.UUID,
                  period: str = "DAY") -> models.TechnicianKPI:
        tenant_id = ctx.require_tenant()
        since = _utcnow() - timedelta(days=30 if period == "MONTH" else 1)
        completed = session.query(models.WorkOrder).filter(
            models.WorkOrder.tenant_id == tenant_id,
            models.WorkOrder.technician_id == tech_id,
            models.WorkOrder.status == "COMPLETED",
            models.WorkOrder.completed_at >= since).count()
        feedbacks = session.query(models.Feedback).join(
            models.WorkOrder, models.WorkOrder.id == models.Feedback.work_order_id).filter(
            models.WorkOrder.tenant_id == tenant_id,
            models.WorkOrder.technician_id == tech_id,
            models.Feedback.created_at >= since).all()
        avg_rating = round(sum(f.rating for f in feedbacks) / len(feedbacks), 2) if feedbacks else 0.0
        slas = session.query(models.FieldSLA).filter(
            models.FieldSLA.tenant_id == tenant_id,
            models.FieldSLA.checked_at.isnot(None)).count()
        breached = session.query(models.FieldSLA).filter(
            models.FieldSLA.tenant_id == tenant_id,
            models.FieldSLA.breached.is_(True),
            models.FieldSLA.checked_at.isnot(None)).count()
        sla_pct = round(100 * (1 - breached / slas), 2) if slas else 100.0
        productivity = round(avg_rating * 0.4 + sla_pct * 0.3 + min(completed, 10) * 3.0, 2)
        row = session.query(models.TechnicianKPI).filter(
            models.TechnicianKPI.tenant_id == tenant_id,
            models.TechnicianKPI.technician_id == tech_id,
            models.TechnicianKPI.period == period).first()
        if row:
            row.jobs_completed, row.avg_rating = completed, avg_rating
            row.sla_compliance_pct, row.productivity_score = sla_pct, productivity
            row.computed_at = _utcnow()
        else:
            row = models.TechnicianKPI(tenant_id=tenant_id, technician_id=tech_id,
                                       period=period, jobs_completed=completed,
                                       avg_rating=avg_rating, sla_compliance_pct=sla_pct,
                                       productivity_score=productivity)
            session.add(row)
        session.commit()
        return row
