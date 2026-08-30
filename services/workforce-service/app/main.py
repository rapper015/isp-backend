"""Workforce Service API (Master Spec Batch 2: field operations)."""
import json
import uuid
from datetime import datetime, timezone
from os import getenv

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from . import events as ev
from . import models, schemas
from .context import TenantContext
from .database import SessionLocal
from .routing import enforce_scope, record_audit, require_tenant_id
from .security import _required_permission, get_auth_context, require_permission
from .services import (ChecklistService, DispatchService, EquipmentOverlayService,
                       EscalationService, ExpertService, FailureVisualizationService,
                       FeedbackService, FieldOpsService, InventoryService,
                       KpiService, ShiftService, SlaService, SparePartService,
                       TechnicianService, VisitService, WorkOrderService)


def _db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _auth(perm: str):
    def dep(request: Request, ctx: TenantContext = Depends(get_auth_context)) -> TenantContext:
        require_permission(ctx, perm)
        return ctx
    return dep


app = FastAPI(title="Workforce Service", version="0.2.0", description="Field operations, dispatch, inventory, SLA/KPI")


@app.get("/health")
def health():
    return {"status": "ok", "service": getenv("SERVICE_NAME", "workforce-service")}


@app.get("/status")
def status():
    return {"service": "workforce", "phase": "batch2", "published_events": ev.ALL_PUBLISHED}


# ---------------------------------------------------------------------------
# Technicians (features 329-330 foundation, 344 shifts, 346 KPI)
# ---------------------------------------------------------------------------
@app.post("/api/workforce/v1/technicians", status_code=201)
def create_technician(body: schemas.TechnicianIn, request: Request,
                      db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("technicians.manage"))):
    require_tenant_id(ctx)
    t = TechnicianService.create(db, ctx, body.model_dump())
    return schemas.TechnicianOut.model_validate(t)


@app.get("/api/workforce/v1/technicians")
def list_technicians(request: Request, status: str | None = None,
                     db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("technicians.view"))):
    q = enforce_scope(db.query(models.Technician), models.Technician, ctx)
    if status:
        q = q.filter(models.Technician.status == status)
    return [schemas.TechnicianOut.model_validate(t) for t in q.order_by(models.Technician.name).all()]


@app.post("/api/workforce/v1/technicians/{tech_id}/status")
def set_technician_status(tech_id: uuid.UUID, request: Request, payload: dict,
                          db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("technicians.manage"))):
    try:
        t = TechnicianService.set_status(db, ctx, tech_id, payload.get("status", ""))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return schemas.TechnicianOut.model_validate(t)


@app.post("/api/workforce/v1/internal/ingest/location")
def ingest_location(body: schemas.LocationIn, request: Request,
                    ctx: TenantContext = Depends(_auth("location.ingest"))):
    """GPS tracking ingest (feature 342) from the technician app / internal key."""
    db = SessionLocal()
    try:
        t = TechnicianService.update_location(db, ctx, body.technician_id, body.lat, body.lon)
        return {"technician_id": str(t.id), "lat": t.last_lat, "lon": t.last_lon,
                "updated_at": t.location_updated_at}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Work orders (features 329, 330, 333, 334, 349)
# ---------------------------------------------------------------------------
@app.post("/api/workforce/v1/work-orders", status_code=201)
def create_work_order(body: schemas.WorkOrderIn, request: Request,
                      db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("workorders.manage"))):
    require_tenant_id(ctx)
    wo = WorkOrderService.create(db, ctx, body.model_dump())
    return schemas.WorkOrderOut.model_validate(wo)


@app.get("/api/workforce/v1/work-orders")
def list_work_orders(request: Request, status: str | None = None, type: str | None = None,
                     limit: int = Query(200, le=1000),
                     db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("workorders.view"))):
    q = enforce_scope(db.query(models.WorkOrder), models.WorkOrder, ctx)
    if status:
        q = q.filter(models.WorkOrder.status == status)
    if type:
        q = q.filter(models.WorkOrder.type == type)
    return [schemas.WorkOrderOut.model_validate(wo) for wo in
            q.order_by(models.WorkOrder.created_at.desc()).limit(limit).all()]


@app.get("/api/workforce/v1/work-orders/{wo_id}")
def get_work_order(wo_id: uuid.UUID, request: Request, db: Session = Depends(_db),
                   ctx: TenantContext = Depends(_auth("workorders.view"))):
    wo = enforce_scope(db.query(models.WorkOrder).filter(
        models.WorkOrder.id == wo_id), models.WorkOrder, ctx).first()
    if not wo:
        raise HTTPException(status_code=404, detail="Work order not found")
    return schemas.WorkOrderOut.model_validate(wo)


@app.post("/api/workforce/v1/work-orders/{wo_id}/assign")
def assign_work_order(wo_id: uuid.UUID, body: schemas.AssignIn, request: Request,
                      db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("dispatch.manage"))):
    try:
        wo = WorkOrderService.assign(db, ctx, wo_id, body.technician_id, body.notes,
                                     body.scheduled_start, body.scheduled_end)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return schemas.WorkOrderOut.model_validate(wo)


@app.post("/api/workforce/v1/work-orders/{wo_id}/dispatch")
def dispatch_work_order(wo_id: uuid.UUID, body: schemas.DispatchIn, request: Request,
                        db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("dispatch.manage"))):
    try:
        wo = WorkOrderService.dispatch(db, ctx, wo_id, body.notes)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return schemas.WorkOrderOut.model_validate(wo)


@app.post("/api/workforce/v1/work-orders/{wo_id}/transition")
def transition_work_order(wo_id: uuid.UUID, body: schemas.TransitionIn, request: Request,
                          db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("workorders.manage"))):
    try:
        wo = WorkOrderService.transition(db, ctx, wo_id, body.transition, body.note)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return schemas.WorkOrderOut.model_validate(wo)


@app.post("/api/workforce/v1/work-orders/{wo_id}/complete")
def complete_work_order(wo_id: uuid.UUID, payload: dict = None, request: Request = None,
                        db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("workorders.manage"))):
    payload = payload or {}
    try:
        wo = WorkOrderService.complete(db, ctx, wo_id, payload.get("note"))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return schemas.WorkOrderOut.model_validate(wo)


@app.get("/api/workforce/v1/dispatch/suggest")
def suggest_technicians(request: Request, work_order_id: uuid.UUID,
                        skills: str | None = None,
                        db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("dispatch.manage"))):
    skill_list = skills.split(",") if skills else None
    candidates = DispatchService.suggest(db, ctx, work_order_id, skill_list)
    return [schemas.TechnicianOut.model_validate(t) for t in candidates]


# ---------------------------------------------------------------------------
# Field ops: checklist, site checks, visits, proof, handover (features 1111-1116, 1119)
# ---------------------------------------------------------------------------
@app.post("/api/workforce/v1/checklist-templates", status_code=201)
def set_checklist_template(request: Request, payload: dict,
                           db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("fieldops.manage"))):
    t = ChecklistService.set_template(db, ctx, payload.get("work_order_type", "INSTALLATION"),
                                      payload.get("items", []))
    return {"id": str(t.id), "work_order_type": t.work_order_type, "items": t.items}


@app.post("/api/workforce/v1/work-orders/{wo_id}/checklist/validate")
def validate_checklist(wo_id: uuid.UUID, body: schemas.ChecklistValidateIn, request: Request,
                       db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("fieldops.manage"))):
    try:
        valid, missing = ChecklistService.validate(db, ctx, wo_id, body.completed)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    if not valid:
        raise HTTPException(status_code=422, detail={"valid": False, "missing": missing})
    return {"valid": True, "missing": missing}


@app.post("/api/workforce/v1/work-orders/{wo_id}/site-checks", status_code=201)
def add_site_check(wo_id: uuid.UUID, body: schemas.SiteCheckIn, request: Request,
                   db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("fieldops.manage"))):
    c = FieldOpsService.site_check(db, ctx, wo_id, body.kind, body.passed, body.details)
    return {"id": str(c.id), "kind": c.kind, "passed": c.passed}


@app.get("/api/workforce/v1/work-orders/{wo_id}/site-checks")
def list_site_checks(wo_id: uuid.UUID, request: Request, db: Session = Depends(_db),
                     ctx: TenantContext = Depends(_auth("fieldops.manage"))):
    q = enforce_scope(db.query(models.SiteCheck).filter(
        models.SiteCheck.work_order_id == wo_id), models.SiteCheck, ctx)
    return [{"id": str(c.id), "kind": c.kind, "passed": c.passed, "details": c.details,
             "checked_at": c.checked_at} for c in q.all()]


@app.post("/api/workforce/v1/work-orders/{wo_id}/visits", status_code=201)
def record_visit(wo_id: uuid.UUID, request: Request, payload: dict,
                 db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("visits.manage"))):
    v = VisitService.record(db, ctx, wo_id, uuid.UUID(payload.get("technician_id")),
                            payload.get("visit_type", "SITE"), payload.get("lat"),
                            payload.get("lon"), payload.get("notes"))
    return {"id": str(v.id), "visit_type": v.visit_type, "created_at": v.created_at}


@app.post("/api/workforce/v1/work-orders/{wo_id}/proof", status_code=201)
def add_proof(wo_id: uuid.UUID, body: schemas.ProofIn, request: Request,
              db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("proof.manage"))):
    from sqlalchemy.exc import IntegrityError
    try:
        p = VisitService.add_proof(db, ctx, wo_id, body.kind, body.evidence_key, body.visit_id)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Duplicate evidence key")
    return {"id": str(p.id), "kind": p.kind, "evidence_key": p.evidence_key}


@app.get("/api/workforce/v1/work-orders/{wo_id}/proof")
def list_proof(wo_id: uuid.UUID, request: Request, db: Session = Depends(_db),
               ctx: TenantContext = Depends(_auth("proof.manage"))):
    q = enforce_scope(db.query(models.ProofOfWork).filter(
        models.ProofOfWork.work_order_id == wo_id), models.ProofOfWork, ctx)
    return [{"id": str(p.id), "kind": p.kind, "evidence_key": p.evidence_key,
             "created_at": p.created_at} for p in q.all()]


@app.post("/api/workforce/v1/work-orders/{wo_id}/handover", status_code=201)
def handover(wo_id: uuid.UUID, body: schemas.HandoverIn, request: Request,
             db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("fieldops.manage"))):
    h = FieldOpsService.handover(db, ctx, wo_id, body.accepted_by, body.notes)
    return {"id": str(h.id), "accepted_by": h.accepted_by, "signed_at": h.signed_at}


# ---------------------------------------------------------------------------
# Inventory (features 337, 338, 339)
# ---------------------------------------------------------------------------
@app.post("/api/workforce/v1/inventory/items", status_code=201)
def add_inventory_item(body: schemas.InventoryItemIn, request: Request,
                       db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("inventory.manage"))):
    require_tenant_id(ctx)
    it = InventoryService.add_item(db, ctx, body.model_dump())
    return {"id": str(it.id), "item_type": it.item_type, "status": it.status}


@app.get("/api/workforce/v1/inventory/items")
def list_inventory(request: Request, status: str | None = None,
                   db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("inventory.view"))):
    q = enforce_scope(db.query(models.InventoryItem), models.InventoryItem, ctx)
    if status:
        q = q.filter(models.InventoryItem.status == status)
    return [{"id": str(i.id), "item_type": i.item_type, "serial_number": i.serial_number,
             "mac_address": i.mac_address, "status": i.status} for i in q.all()]


@app.post("/api/workforce/v1/inventory/items/{item_id}/issue")
def issue_item(item_id: uuid.UUID, body: schemas.IssueIn, request: Request,
               db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("inventory.manage"))):
    try:
        it = InventoryService.issue(db, ctx, item_id, body.work_order_id, body.technician_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"id": str(it.id), "status": it.status, "work_order_id": str(it.work_order_id)}


@app.post("/api/workforce/v1/inventory/items/{item_id}/return")
def return_item(item_id: uuid.UUID, request: Request,
                db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("inventory.manage"))):
    try:
        it = InventoryService.return_item(db, ctx, item_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"id": str(it.id), "status": it.status, "returned_at": it.returned_at}


@app.post("/api/workforce/v1/inventory/sync")
def sync_inventory(request: Request, payload: dict,
                   db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("inventory.manage"))):
    return InventoryService.sync_stock(db, ctx, payload.get("stock", []))


@app.post("/api/workforce/v1/inventory/consumables", status_code=201)
def add_consumable(body: schemas.ConsumableIn, request: Request,
                   db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("inventory.manage"))):
    c = InventoryService.add_consumable(db, ctx, body.model_dump())
    return {"id": str(c.id), "name": c.name, "sku": c.sku, "quantity": c.quantity}


@app.post("/api/workforce/v1/inventory/consumables/consume")
def consume_consumable(body: schemas.ConsumeIn, request: Request,
                       db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("inventory.consume"))):
    try:
        c = InventoryService.consume(db, ctx, body.work_order_id, body.sku, body.quantity)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"consumption_id": str(c.id), "sku": body.sku, "quantity": body.quantity}


# ---------------------------------------------------------------------------
# Shifts, feedback, escalations, SLA, KPI (features 344, 348, 349, 347, 346, 1490)
# ---------------------------------------------------------------------------
@app.post("/api/workforce/v1/shifts", status_code=201)
def create_shift(body: schemas.ShiftIn, request: Request,
                 db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("shifts.manage"))):
    s = ShiftService.create(db, ctx, body.model_dump())
    return {"id": str(s.id), "technician_id": str(s.technician_id), "status": s.status}


@app.post("/api/workforce/v1/work-orders/{wo_id}/feedback", status_code=201)
def submit_feedback(wo_id: uuid.UUID, body: schemas.FeedbackIn, request: Request,
                    db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("workorders.manage"))):
    f = FeedbackService.submit(db, ctx, wo_id, body.rating, body.comment)
    return {"id": str(f.id), "rating": f.rating}


@app.get("/api/workforce/v1/feedback")
def list_feedback(request: Request, db: Session = Depends(_db),
                  ctx: TenantContext = Depends(_auth("feedback.view"))):
    q = enforce_scope(db.query(models.Feedback), models.Feedback, ctx)
    return [{"id": str(f.id), "work_order_id": str(f.work_order_id), "rating": f.rating,
             "comment": f.comment, "created_at": f.created_at} for f in
            q.order_by(models.Feedback.created_at.desc()).limit(200).all()]


@app.post("/api/workforce/v1/escalations", status_code=201)
def create_escalation(body: schemas.EscalationIn, request: Request,
                      db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("escalations.manage"))):
    e = EscalationService.create(db, ctx, body.work_order_id, body.level, body.reason)
    return {"id": str(e.id), "level": e.level, "status": e.status}


@app.post("/api/workforce/v1/escalations/{esc_id}/resolve")
def resolve_escalation(esc_id: uuid.UUID, request: Request,
                       db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("escalations.manage"))):
    try:
        e = EscalationService.resolve(db, ctx, esc_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"id": str(e.id), "status": e.status}


@app.post("/api/workforce/v1/sla/evaluate")
def evaluate_sla(request: Request, db: Session = Depends(_db),
                 ctx: TenantContext = Depends(_auth("sla.view"))):
    return SlaService.evaluate(db, ctx)


@app.get("/api/workforce/v1/kpis/technician/{tech_id}")
def get_kpi(tech_id: uuid.UUID, request: Request, period: str = "DAY",
            db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("kpi.view"))):
    q = enforce_scope(db.query(models.TechnicianKPI).filter(
        models.TechnicianKPI.technician_id == tech_id,
        models.TechnicianKPI.period == period), models.TechnicianKPI, ctx)
    row = q.first()
    if not row:
        raise HTTPException(status_code=404, detail="KPI not computed yet")
    return {"technician_id": str(row.technician_id), "period": row.period,
            "jobs_completed": row.jobs_completed, "avg_rating": row.avg_rating,
            "sla_compliance_pct": row.sla_compliance_pct,
            "productivity_score": row.productivity_score, "computed_at": row.computed_at}


@app.get("/api/workforce/v1/dashboard/summary")
def dashboard_summary(request: Request, db: Session = Depends(_db),
                      ctx: TenantContext = Depends(_auth("dashboard.view"))):
    wo_q = enforce_scope(db.query(models.WorkOrder), models.WorkOrder, ctx)
    tech_q = enforce_scope(db.query(models.Technician), models.Technician, ctx)
    esc_q = enforce_scope(db.query(models.Escalation), models.Escalation, ctx)
    return {
        "total_work_orders": wo_q.count(),
        "open_work_orders": wo_q.filter(models.WorkOrder.status.notin_(
            ["COMPLETED", "CANCELLED"])).count(),
        "available_technicians": tech_q.filter(models.Technician.status == "AVAILABLE").count(),
        "open_escalations": esc_q.filter(models.Escalation.status == "OPEN").count(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/workforce/v1/audit-log")
def list_audit(request: Request, action: str | None = None, limit: int = Query(200, le=1000),
               db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("audit.view"))):
    q = enforce_scope(db.query(models.WorkforceAuditLog), models.WorkforceAuditLog, ctx)
    if action:
        q = q.filter(models.WorkforceAuditLog.action == action)
    rows = q.order_by(models.WorkforceAuditLog.created_at.desc()).limit(limit).all()
    return [{"id": str(r.id), "actor": r.actor, "action": r.action, "resource": r.resource,
             "resource_id": r.resource_id, "outcome": r.outcome, "detail": r.detail,
             "created_at": r.created_at} for r in rows]


# ---------------------------------------------------------------------------
# Remote expert assistance + failure visualization + AR overlay (Batch 8)
# ---------------------------------------------------------------------------

@app.post("/api/workforce/v1/expert/sessions", status_code=201)
def start_expert_session(body: dict, request: Request,
                         db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("workorders.manage"))):
    s = ExpertService.start(db, ctx, body)
    return {"id": str(s.id), "work_order_id": s.work_order_id, "expert_id": s.expert_id,
            "channel": s.channel, "status": s.status}


@app.get("/api/workforce/v1/expert/sessions")
def list_expert_sessions(request: Request, db: Session = Depends(_db),
                         ctx: TenantContext = Depends(_auth("workorders.view"))):
    q = enforce_scope(db.query(models.ExpertSession), models.ExpertSession, ctx)
    return [{"id": str(r.id), "work_order_id": r.work_order_id, "expert_id": r.expert_id,
             "channel": r.channel, "status": r.status} for r in q.all()]


@app.post("/api/workforce/v1/expert/sessions/{session_id}/end")
def end_expert_session(session_id: uuid.UUID, request: Request,
                       db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("workorders.manage"))):
    try:
        s = ExpertService.end(db, ctx, session_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"id": str(s.id), "status": s.status}


@app.post("/api/workforce/v1/failure/visualizations", status_code=201)
def render_failure_visualization(body: dict, request: Request,
                                 db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("workorders.manage"))):
    v = FailureVisualizationService.render(db, ctx, body)
    return {"id": str(v.id), "work_order_id": v.work_order_id, "fault_type": v.fault_type,
            "rendered": v.rendered}


@app.get("/api/workforce/v1/failure/visualizations")
def list_failure_visualizations(request: Request, db: Session = Depends(_db),
                                ctx: TenantContext = Depends(_auth("workorders.view"))):
    q = enforce_scope(db.query(models.FailureVisualization), models.FailureVisualization, ctx)
    return [{"id": str(r.id), "work_order_id": r.work_order_id, "fault_type": r.fault_type,
             "rendered": r.rendered} for r in q.all()]


@app.post("/api/workforce/v1/failure/visualizations/{vis_id}/rendered")
def mark_visualization_rendered(vis_id: uuid.UUID, request: Request,
                                db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("workorders.manage"))):
    try:
        v = FailureVisualizationService.mark_rendered(db, ctx, vis_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"id": str(v.id), "rendered": v.rendered}


@app.post("/api/workforce/v1/equipment/overlays", status_code=201)
def recognize_equipment(body: dict, request: Request,
                        db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("workorders.manage"))):
    o = EquipmentOverlayService.recognize(db, ctx, body)
    return {"id": str(o.id), "work_order_id": o.work_order_id, "device_id": o.device_id,
            "recognized_model": o.recognized_model}


@app.get("/api/workforce/v1/equipment/overlays")
def list_equipment_overlays(request: Request, db: Session = Depends(_db),
                            ctx: TenantContext = Depends(_auth("workorders.view"))):
    q = enforce_scope(db.query(models.EquipmentOverlay), models.EquipmentOverlay, ctx)
    return [{"id": str(r.id), "work_order_id": r.work_order_id, "device_id": r.device_id,
             "recognized_model": r.recognized_model} for r in q.all()]


# ---------------------------------------------------------------------------
# Spare parts management (feature 338)
# ---------------------------------------------------------------------------

@app.post("/api/workforce/v1/spare-parts", status_code=201)
def register_spare_part(body: dict, request: Request,
                        db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("inventory.manage"))):
    p = SparePartService.register(db, ctx, body)
    return {"id": str(p.id), "part_code": p.part_code, "name": p.name,
            "quantity": p.quantity, "min_stock": p.min_stock, "status": p.status}


@app.get("/api/workforce/v1/spare-parts")
def list_spare_parts(request: Request, db: Session = Depends(_db),
                     ctx: TenantContext = Depends(_auth("inventory.view"))):
    q = enforce_scope(db.query(models.SparePart), models.SparePart, ctx)
    return [{"id": str(r.id), "part_code": r.part_code, "name": r.name,
             "quantity": r.quantity, "min_stock": r.min_stock,
             "used_count": r.used_count, "status": r.status} for r in q.all()]


@app.post("/api/workforce/v1/spare-parts/{part_id}/use")
def use_spare_part(part_id: uuid.UUID, body: dict, request: Request,
                   db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("inventory.consume"))):
    try:
        p = SparePartService.use(db, ctx, part_id, int(body.get("quantity", 1)))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"id": str(p.id), "part_code": p.part_code, "quantity": p.quantity,
            "used_count": p.used_count, "status": p.status}
