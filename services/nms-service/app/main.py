from contextlib import asynccontextmanager
from datetime import datetime,timezone
from os import getenv
from uuid import UUID
import uuid
from fastapi import Depends,FastAPI,HTTPException,Request
from pydantic import BaseModel,ConfigDict
from sqlalchemy.orm import Session
from .database import Base,SessionLocal,engine
from . import models
from .models import AnomalyHeatmap,HealthObservation,NasDevice,Runbook
from .security import management_auth
from .services import OpsService
@asynccontextmanager
async def lifespan(_):Base.metadata.create_all(bind=engine);yield
app=FastAPI(title='NMS Service',version='0.2.0',lifespan=lifespan)
def db():
 s=SessionLocal()
 try:yield s
 finally:s.close()

class NasIn(BaseModel):name:str;host:str
class NasOut(NasIn):model_config=ConfigDict(from_attributes=True);id:UUID;status:str
class ObservationIn(BaseModel):status:str;detail:str|None=None

@app.get('/health')
def health():return {'status':'ok','service':getenv('SERVICE_NAME','nms-service')}
@app.get('/status')
def service_status():return {'service':'nms','phase':'monitoring-api'}

@app.post('/devices',response_model=NasOut)
def create(p:NasIn,s:Session=Depends(db)):
 x=NasDevice(**p.model_dump());s.add(x);s.commit();s.refresh(x);return x

@app.post('/devices/{device_id}/observations')
def observe(device_id:UUID,p:ObservationIn,s:Session=Depends(db)):
 x=s.get(NasDevice,device_id)
 if not x:raise HTTPException(404,'device not found')
 x.status=p.status;x.last_checked_at=datetime.now(timezone.utc);o=HealthObservation(nas_id=x.id,**p.model_dump());s.add(o);s.commit();return {'status':x.status}


# ===========================================================================
# NMS operations API (Batch 7c: 266, 1082, 1124, 1167, 1286, 1344)
# ===========================================================================

def _tid(request) -> UUID:
    tid = getattr(request.state, "nms_principal", {}).get("tenant_id")
    if not tid:
        raise HTTPException(400, "tenant_id required")
    return UUID(str(tid))


@app.post("/api/nms/ops/escalation-policies", dependencies=[Depends(management_auth)])
def create_escalation_policy(payload: dict, request: Request, s: Session = Depends(db)):
    row = OpsService.create_escalation_policy(s, _tid(request), payload)
    return {"id": str(row.id), "name": row.name, "rule_json": row.rule_json}


@app.get("/api/nms/ops/escalation-policies", dependencies=[Depends(management_auth)])
def list_escalation_policies(request: Request, s: Session = Depends(db)):
    tid = _tid(request)
    from . import models
    rows = s.query(models.EscalationPolicy).filter(models.EscalationPolicy.tenant_id == tid).all()
    return [{"id": str(r.id), "name": r.name, "rule_json": r.rule_json, "enabled": r.enabled} for r in rows]


@app.post("/api/nms/ops/config/snapshot", dependencies=[Depends(management_auth)])
def save_snapshot(payload: dict, request: Request, s: Session = Depends(db)):
    row = OpsService.save_snapshot(s, _tid(request), payload.get("device_id"),
                                   payload.get("label", "CURRENT"), payload.get("config", ""))
    return {"id": str(row.id), "device_id": row.device_id, "label": row.label}


@app.post("/api/nms/ops/config/diff", dependencies=[Depends(management_auth)])
def config_diff(payload: dict, request: Request, s: Session = Depends(db)):
    try:
        return OpsService.config_diff(s, _tid(request), payload.get("device_id"))
    except KeyError as e:
        raise HTTPException(404, str(e))


@app.post("/api/nms/ops/approval-sla", dependencies=[Depends(management_auth)])
def set_approval_sla(payload: dict, request: Request, s: Session = Depends(db)):
    row = OpsService.set_approval_sla(s, _tid(request), payload)
    return {"id": str(row.id), "approval_type": row.approval_type, "sla_minutes": row.sla_minutes}


@app.post("/api/nms/ops/approval-sla/overdue", dependencies=[Depends(management_auth)])
def record_overdue(payload: dict, request: Request, s: Session = Depends(db)):
    try:
        row = OpsService.record_overdue(s, _tid(request), payload.get("approval_type"),
                                        int(payload.get("minutes", 0)))
    except KeyError as e:
        raise HTTPException(404, str(e))
    return {"approval_type": row.approval_type, "overdue_count": row.overdue_count}


@app.post("/api/nms/ops/cache-strategies", dependencies=[Depends(management_auth)])
def set_cache_strategy(payload: dict, request: Request, s: Session = Depends(db)):
    row = OpsService.set_cache_strategy(s, _tid(request), payload)
    return {"id": str(row.id), "cache_key": row.cache_key, "ttl_seconds": row.ttl_seconds,
            "strategy": row.strategy}


@app.post("/api/nms/ops/degradation-rules", dependencies=[Depends(management_auth)])
def apply_degradation(payload: dict, request: Request, s: Session = Depends(db)):
    row = OpsService.apply_degradation(s, _tid(request), payload)
    return {"id": str(row.id), "service": row.service, "degraded_mode": row.degraded_mode,
            "keep_alive_pct": row.keep_alive_pct, "enabled": row.enabled}


@app.post("/api/nms/ops/queue-saturation", dependencies=[Depends(management_auth)])
def protect_queue(payload: dict, request: Request, s: Session = Depends(db)):
    row = OpsService.protect_queue(s, _tid(request), payload)
    return {"id": str(row.id), "queue": row.queue, "depth": row.depth,
            "max_depth": row.max_depth, "protected": row.protected}


@app.post("/api/nms/ops/runbooks", status_code=201, dependencies=[Depends(management_auth)])
def create_runbook(payload: dict, request: Request, s: Session = Depends(db)):
    row = OpsService.create_runbook(s, _tid(request), payload)
    return {"id": str(row.id), "name": row.name, "trigger": row.trigger,
            "steps": len(row.steps or []), "executions": row.executions,
            "status": row.status}


@app.get("/api/nms/ops/runbooks", dependencies=[Depends(management_auth)])
def list_runbooks(request: Request, s: Session = Depends(db)):
    tid = _tid(request)
    rows = s.query(models.Runbook).filter(models.Runbook.tenant_id == tid).all()
    return [{"id": str(r.id), "name": r.name, "trigger": r.trigger,
             "executions": r.executions, "status": r.status} for r in rows]


@app.post("/api/nms/ops/runbooks/{runbook_id}/trigger", dependencies=[Depends(management_auth)])
def trigger_runbook(runbook_id: uuid.UUID, request: Request, s: Session = Depends(db)):
    try:
        row = OpsService.trigger_runbook(s, _tid(request), runbook_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Runbook not found")
    return {"id": str(row.id), "name": row.name, "executions": row.executions}


@app.post("/api/nms/ops/anomaly/heatmap", status_code=201, dependencies=[Depends(management_auth)])
def generate_heatmap(payload: dict, request: Request, s: Session = Depends(db)):
    row = OpsService.generate_heatmap(s, _tid(request), payload)
    return {"id": str(row.id), "scope": row.scope, "period": row.period,
            "cells": len(row.cells or []), "anomaly_count": row.anomaly_count}


@app.get("/api/nms/ops/anomaly/heatmaps", dependencies=[Depends(management_auth)])
def list_heatmaps(request: Request, s: Session = Depends(db)):
    tid = _tid(request)
    rows = s.query(models.AnomalyHeatmap).filter(models.AnomalyHeatmap.tenant_id == tid).all()
    return [{"id": str(r.id), "scope": r.scope, "period": r.period,
             "cells": len(r.cells or []), "anomaly_count": r.anomaly_count} for r in rows]
