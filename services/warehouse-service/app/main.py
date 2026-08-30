"""Data Warehouse Service - analytics API (Master Spec Batch 7d)."""
import uuid
from os import getenv

from fastapi import Depends, FastAPI, Request
from sqlalchemy.orm import Session

from .database import Base, SessionLocal, engine
from .models import AnalyticsCluster, EcosystemMetric, Kpi, Profitability, RevenueTrend
from .security import management_auth
from .services import AnalyticsService

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Data Warehouse Service", version="1.0.0")


def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _tid(request: Request):
    raw = request.state.wh_principal["tenant_id"]
    return uuid.UUID(str(raw)) if not isinstance(raw, uuid.UUID) else raw


svc = AnalyticsService()


@app.get("/api/warehouse/health")
def health():
    return {"status": "ok", "service": getenv("SERVICE_NAME", "warehouse-service")}


# 468 KPI Management
@app.post("/api/warehouse/kpis", dependencies=[Depends(management_auth)])
def upsert_kpi(req: Request, body: dict, s: Session = Depends(db)):
    return svc.upsert_kpi(s, _tid(req), body)


@app.get("/api/warehouse/kpis", dependencies=[Depends(management_auth)])
def list_kpis(req: Request, s: Session = Depends(db)):
    t = _tid(req)
    return [{"code": r.code, "name": r.name, "category": r.category, "target": r.target, "unit": r.unit, "status": r.status}
            for r in s.query(Kpi).filter_by(tenant_id=t).all()]


# 477 Revenue Trends
@app.post("/api/warehouse/revenue/trends", dependencies=[Depends(management_auth)])
def record_revenue(req: Request, body: dict, s: Session = Depends(db)):
    return svc.record_revenue(s, _tid(req), body)


@app.get("/api/warehouse/revenue/trends", dependencies=[Depends(management_auth)])
def list_revenue(req: Request, s: Session = Depends(db)):
    t = _tid(req)
    return [{"stream": r.stream, "period": r.period, "amount": r.amount, "trend": r.trend}
            for r in s.query(RevenueTrend).filter_by(tenant_id=t).all()]


# 478 Profitability Analysis
@app.post("/api/warehouse/profitability", dependencies=[Depends(management_auth)])
def record_profitability(req: Request, body: dict, s: Session = Depends(db)):
    return svc.record_profitability(s, _tid(req), body)


@app.get("/api/warehouse/profitability", dependencies=[Depends(management_auth)])
def list_profitability(req: Request, s: Session = Depends(db)):
    t = _tid(req)
    return [{"segment": r.segment, "period": r.period, "revenue": r.revenue, "cost": r.cost, "margin_pct": r.margin_pct}
            for r in s.query(Profitability).filter_by(tenant_id=t).all()]


# 499 Horizontal Scaling
@app.post("/api/warehouse/cluster/scale", dependencies=[Depends(management_auth)])
def scale_cluster(req: Request, body: dict, s: Session = Depends(db)):
    return svc.scale_cluster(s, _tid(req), body)


@app.get("/api/warehouse/cluster/nodes", dependencies=[Depends(management_auth)])
def list_cluster(req: Request, s: Session = Depends(db)):
    t = _tid(req)
    return [{"node": r.node, "role": r.role, "status": r.status, "load": r.load}
            for r in s.query(AnalyticsCluster).filter_by(tenant_id=t).all()]


# 839 Ecosystem Analytics
@app.post("/api/warehouse/ecosystem/metrics", dependencies=[Depends(management_auth)])
def record_ecosystem(req: Request, body: dict, s: Session = Depends(db)):
    return svc.record_ecosystem(s, _tid(req), body)


@app.get("/api/warehouse/ecosystem/metrics", dependencies=[Depends(management_auth)])
def list_ecosystem(req: Request, s: Session = Depends(db)):
    t = _tid(req)
    return [{"partner": r.partner, "period": r.period, "metric": r.metric, "value": r.value}
            for r in s.query(EcosystemMetric).filter_by(tenant_id=t).all()]


@app.get("/status")
def status():
    return {"service": "warehouse", "phase": "analytics"}
