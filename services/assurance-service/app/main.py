"""Assurance Service — observability & service assurance layer.

FastAPI application exposing the governance surface: service catalogue,
SLIs/SLOs/error budgets, alert lifecycle, incidents, root cause, postmortems,
KPIs, maintenance windows, synthetic checks and reports.

Tenant-aware: tenant-owned data requires a validated TenantContext; platform
aggregates require explicit PLATFORM_AGGREGATE scope.
"""
from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from . import schemas
from .cache import limited
from .context import get_context, require_tenant, reset_context, set_context
from .database import Base, SessionLocal, engine
from .domain.exceptions import (AssuranceError, ElevatedPermissionRequiredError,
                                TenantContextRequiredError, TenantIsolationError)
from .messaging import consumers
from .models import (Alert, AlertRoute, AlertSilence, Incident, IncidentEvent,  # noqa: F401
                     KpiDefinition, MaintenanceWindow, MetricRegistry, NetworkObservation,
                     Postmortem, RootCauseEvidence, RootCauseHypothesis, ServiceDefinition,
                     SlIDefinition, SloDefinition, SloVersion, SloWindowState, SyntheticCheck,
                     SyntheticResult)
from .routing import enforce_scope, require_platform_aggregate
from .schemas import (AlertIngestIn, AlertRouteIn, AlertSilenceIn, CommunicationIn, ConfirmRootCauseIn,
                      EvidenceIn, HypothesisIn, ImpactConfirmIn, ImpactEstimateIn, IncidentActionIn,
                      IncidentCreateIn, IncidentTransitionIn, KpiDefinitionIn, KpiMeasurementIn,
                      KpiTargetIn, MaintenanceWindowIn, NetworkObservationIn, PostmortemActionIn,
                      PostmortemCreateIn, ServiceDefinitionIn, SlIDefinitionIn, SlIMeasurementIn,
                      SloCreateIn, SyntheticCheckIn, SyntheticResultIn)
from .security import current_tenant, internal_service_auth, management_auth
from .services import (alert_service, audit_service, catalog_service, incident_service, kpi_service,
                       maintenance_service, report_service, slo_service, synthetic_service)

logger = logging.getLogger("assurance")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid(value) -> uuid.UUID:
    return uuid.UUID(str(value))


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as session:
        catalog_service.ensure_defaults(session)
        session.commit()
    yield


app = FastAPI(title="Assurance Service", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def db():
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _raise(exc) -> None:
    raise exc


def _actor(request: Request) -> str | None:
    principal = getattr(request.state, "assurance_principal", None)
    return principal.get("subject") if principal else None


def _tid(body_tenant=None) -> uuid.UUID | None:
    ctx = get_context()
    if body_tenant is not None:
        return _uuid(body_tenant)
    return ctx.tenant_id if ctx else None


@app.exception_handler(AssuranceError)
async def assurance_error_handler(request: Request, exc: AssuranceError):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message, "code": exc.code})


@app.exception_handler(TenantContextRequiredError)
async def tenant_required_handler(request: Request, exc: TenantContextRequiredError):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=422, content={"detail": exc.message, "code": "TENANT_CONTEXT_REQUIRED"})


@app.exception_handler(TenantIsolationError)
async def isolation_handler(request: Request, exc: TenantIsolationError):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=403, content={"detail": exc.message, "code": "TENANT_ISOLATION"})


@app.get("/health")
def health():
    return {"status": "ok", "service": "assurance"}


@app.get("/status")
def status(db: Session = Depends(db)):
    try:
        db.execute(__import__("sqlalchemy", fromlist=["text"]).text("SELECT 1"))
        db_ready = True
    except Exception:  # noqa: BLE001
        db_ready = False
    return {"status": "ok" if db_ready else "degraded", "service": "assurance", "db": db_ready,
            "timestamp": _now().isoformat()}


# =====================================================================
# Internal ingest (service-to-service)
# =====================================================================
@app.post("/internal/assurance/v1/ingest/alert", dependencies=[Depends(internal_service_auth)])
def ingest_alert(payload: AlertIngestIn, db: Session = Depends(db)):
    ctx = require_tenant() if get_context() and get_context().tenant_id else get_context()
    tenant_id = _tid()
    alert = alert_service.normalize_and_ingest(
        db, service=payload.service, alert_name=payload.alert_name, tenant_id=tenant_id,
        severity=payload.severity, component=payload.component, resource=payload.resource,
        labels=payload.labels, impact=payload.impact, source=payload.source,
        correlation_id=payload.correlation_id, observed_at=payload.observed_at)
    db.commit()
    return {"ok": True, "alert_id": str(alert.id), "fingerprint": alert.fingerprint,
            "state": alert.state, "tenant_id": str(tenant_id) if tenant_id else None}


@app.post("/internal/assurance/v1/ingest/observation", dependencies=[Depends(internal_service_auth)])
def ingest_observation(payload: NetworkObservationIn, db: Session = Depends(db)):
    tenant_id = _tid()
    row = NetworkObservation(tenant_id=tenant_id, device_ref=payload.device_ref,
                             check_type=payload.check_type, status=payload.status,
                             latency_ms=payload.latency_ms, metrics=payload.metrics,
                             observed_at=payload.metrics.get("observed_at", _now()) if isinstance(payload.metrics, dict) else _now(),
                             source=payload.source)
    db.add(row)
    db.commit()
    return {"ok": True, "observation_id": str(row.id)}


@app.post("/internal/assurance/v1/ingest/event", dependencies=[Depends(internal_service_auth)])
def ingest_event(envelope: dict, db: Session = Depends(db)):
    consumers.handle(db, envelope)
    db.commit()
    return {"ok": True, "event_id": envelope.get("event_id")}


# =====================================================================
# Service catalogue
# =====================================================================
@app.get("/api/assurance/v1/services", dependencies=[Depends(management_auth)])
def list_services(db: Session = Depends(db)):
    return [{"id": str(s.id), "code": s.code, "name": s.name, "criticality": s.criticality,
             "tier": s.tier, "owner_team": s.owner_team, "status": s.status} for s in
            db.query(ServiceDefinition).all()]


@app.post("/api/assurance/v1/services", dependencies=[Depends(management_auth)])
def create_service(payload: ServiceDefinitionIn, db: Session = Depends(db)):
    row = ServiceDefinition(code=payload.code, name=payload.name, criticality=payload.criticality,
                            tier=payload.tier, owner_team=payload.owner_team, status=payload.status)
    db.add(row)
    db.commit()
    return {"id": str(row.id), "code": row.code, "name": row.name}


# =====================================================================
# SLI / SLO
# =====================================================================
@app.get("/api/assurance/v1/slis", dependencies=[Depends(management_auth)])
def list_slis(db: Session = Depends(db)):
    return [{"id": str(s.id), "code": s.code, "name": s.name, "measurement_source": s.measurement_source,
             "unit": s.unit, "validation_status": s.validation_status} for s in db.query(SlIDefinition).all()]


@app.post("/api/assurance/v1/slis", dependencies=[Depends(management_auth)])
def create_sli(payload: SlIDefinitionIn, db: Session = Depends(db)):
    sli = slo_service.create_sli(db, _tid(), payload.model_dump())
    audit_service.audit(db, _tid(), _actor_auto(), "sli.created", resource_type="sli", resource_id=sli.id)
    db.commit()
    return {"id": str(sli.id), "code": sli.code}


@app.post("/api/assurance/v1/sli-measurements", dependencies=[Depends(management_auth)])
def record_sli_measurement(payload: SlIMeasurementIn, db: Session = Depends(db)):
    row = slo_service.record_measurement(db, _tid(), payload.sli_code, good=payload.good,
                                         total=payload.total, window_start=payload.window_start,
                                         window_end=payload.window_end, quality=payload.quality,
                                         excluded_good=payload.excluded_good,
                                         excluded_total=payload.excluded_total,
                                         source_ref=payload.source_ref)
    db.commit()
    return {"ok": True, "measurement_id": str(row.id)}


@app.post("/api/assurance/v1/slos", dependencies=[Depends(management_auth)])
def create_slo(payload: SloCreateIn, db: Session = Depends(db)):
    slo = slo_service.create_slo(db, _tid(), payload.model_dump())
    audit_service.audit(db, _tid(), _actor_auto(), "slo.created", resource_type="slo", resource_id=slo.id)
    db.commit()
    return {"id": str(slo.id), "code": slo.code, "state": slo.state}


@app.get("/api/assurance/v1/slos", dependencies=[Depends(management_auth)])
def list_slos(db: Session = Depends(db)):
    out = []
    for s in db.query(SloDefinition).all():
        version = None
        try:
            version = slo_service.latest_version(db, s.id)
        except Exception:  # noqa: BLE001
            pass
        out.append({"id": str(s.id), "code": s.code, "name": s.name, "state": s.state,
                    "version": version.version if version else None,
                    "objective": version.objective if version else None})
    return out


@app.post("/api/assurance/v1/slos/{slo_id}/validate", dependencies=[Depends(management_auth)])
def validate_slo(slo_id: uuid.UUID, db: Session = Depends(db)):
    slo = slo_service.validate_slo(db, slo_id)
    db.commit()
    return {"ok": True, "id": str(slo.id), "state": slo.state}


@app.post("/api/assurance/v1/slos/{slo_id}/approve", dependencies=[Depends(management_auth)])
def approve_slo(slo_id: uuid.UUID, request: Request, db: Session = Depends(db)):
    slo = slo_service.approve_slo(db, slo_id, approved_by=_actor(request) or "system")
    audit_service.audit(db, _tid(), _actor(request), "slo.approved", resource_type="slo", resource_id=slo.id)
    db.commit()
    return {"ok": True, "id": str(slo.id), "state": slo.state}


@app.post("/api/assurance/v1/slos/{slo_id}/activate", dependencies=[Depends(management_auth)])
def activate_slo(slo_id: uuid.UUID, db: Session = Depends(db)):
    slo = slo_service.activate_slo(db, slo_id)
    db.commit()
    return {"ok": True, "id": str(slo.id), "state": slo.state}


@app.get("/api/assurance/v1/slos/{slo_id}/error-budget", dependencies=[Depends(management_auth)])
def error_budget(slo_id: uuid.UUID, db: Session = Depends(db)):
    return slo_service.error_budget(db, slo_id)


@app.post("/api/assurance/v1/slos/{slo_id}/compute-window", dependencies=[Depends(management_auth)])
def compute_window(slo_id: uuid.UUID, db: Session = Depends(db)):
    from .domain.slos import window_bounds
    version = slo_service.latest_version(db, slo_id)
    start, end = window_bounds(_now(), window_type=version.window_type, window_seconds=version.window_seconds)
    state = slo_service.compute_window(db, _tid(), slo_id, window_start=start, window_end=end, force=True)
    db.commit()
    return {"slo_id": str(state.slo_id), "window_start": state.window_start.isoformat(),
            "window_end": state.window_end.isoformat(), "sli_ratio": state.sli_ratio,
            "status": state.status, "burn_rate": state.burn_rate,
            "remaining_budget": state.remaining_budget}


# =====================================================================
# Maintenance windows
# =====================================================================
@app.post("/api/assurance/v1/maintenance", dependencies=[Depends(management_auth)])
def create_maintenance(payload: MaintenanceWindowIn, db: Session = Depends(db)):
    row = maintenance_service.create_window(db, tenant_id=_tid(), service_id=payload.service_id,
                                            starts_at=payload.starts_at, ends_at=payload.ends_at,
                                            maintenance_type=payload.maintenance_type,
                                            reason=payload.reason, owner=payload.owner,
                                            scope_kind=payload.scope_kind, scope_ref=payload.scope_ref,
                                            sla_treatment=payload.sla_treatment,
                                            alert_suppression=payload.alert_suppression)
    audit_service.audit(db, _tid(), _actor_auto(), "maintenance.requested", resource_type="maintenance",
                        resource_id=row.id)
    db.commit()
    return {"id": str(row.id), "state": row.state}


@app.get("/api/assurance/v1/maintenance", dependencies=[Depends(management_auth)])
def list_maintenance(db: Session = Depends(db)):
    return [{"id": str(w.id), "state": w.state, "service_id": str(w.service_id) if w.service_id else None,
             "starts_at": w.starts_at.isoformat(), "ends_at": w.ends_at.isoformat(),
             "maintenance_type": w.maintenance_type} for w in db.query(MaintenanceWindow).all()]


@app.post("/api/assurance/v1/maintenance/{window_id}/approve", dependencies=[Depends(management_auth)])
def approve_maintenance(window_id: uuid.UUID, request: Request, db: Session = Depends(db)):
    w = maintenance_service.approve(db, window_id, approved_by=_actor(request) or "system")
    db.commit()
    return {"ok": True, "id": str(w.id), "state": w.state}


@app.post("/api/assurance/v1/maintenance/{window_id}/cancel", dependencies=[Depends(management_auth)])
def cancel_maintenance(window_id: uuid.UUID, db: Session = Depends(db)):
    w = maintenance_service.cancel(db, window_id)
    db.commit()
    return {"ok": True, "id": str(w.id), "state": w.state}


@app.post("/api/assurance/v1/maintenance/{window_id}/exceptions", dependencies=[Depends(management_auth)])
def add_maintenance_exception(window_id: uuid.UUID, payload: dict, db: Session = Depends(db)):
    row = maintenance_service.add_exception(db, window_id, _uuid(payload["slo_id"]),
                                            approved_by=payload.get("approved_by"),
                                            reason=payload.get("reason"))
    db.commit()
    return {"ok": True, "exception_id": str(row.id)}


# =====================================================================
# Alerts
# =====================================================================
@app.get("/api/assurance/v1/alerts", dependencies=[Depends(management_auth)])
def list_alerts(state: Optional[str] = None, service: Optional[str] = None, db: Session = Depends(db)):
    rows = alert_service.list_alerts(db, _tid(), state=state, service=service)
    return [{"id": str(a.id), "service": a.service, "alert_name": a.alert_name,
             "severity": a.severity, "state": a.state, "fingerprint": a.fingerprint,
             "first_observed": a.first_observed.isoformat() if a.first_observed else None,
             "last_observed": a.last_observed.isoformat() if a.last_observed else None,
             "firing_count": a.firing_count,
             "incident_id": str(a.current_incident_id) if a.current_incident_id else None} for a in rows]


@app.post("/api/assurance/v1/alerts/{alert_id}/acknowledge", dependencies=[Depends(management_auth)])
def acknowledge_alert(alert_id: uuid.UUID, request: Request, db: Session = Depends(db)):
    alert = alert_service.acknowledge(db, alert_id, _actor(request) or "system")
    audit_service.audit(db, _tid(), _actor(request), "alert.acknowledged", resource_type="alert",
                        resource_id=alert.id)
    db.commit()
    return {"ok": True, "id": str(alert.id), "state": alert.state}


@app.post("/api/assurance/v1/alerts/{alert_id}/resolve", dependencies=[Depends(management_auth)])
def resolve_alert(alert_id: uuid.UUID, request: Request, db: Session = Depends(db)):
    alert = alert_service.resolve(db, alert_id, actor=_actor(request))
    audit_service.audit(db, _tid(), _actor(request), "alert.resolved", resource_type="alert",
                        resource_id=alert.id)
    db.commit()
    return {"ok": True, "id": str(alert.id), "state": alert.state}


@app.post("/api/assurance/v1/alerts/{alert_id}/expire", dependencies=[Depends(management_auth)])
def expire_alert(alert_id: uuid.UUID, db: Session = Depends(db)):
    alert = alert_service.expire(db, alert_id)
    db.commit()
    return {"ok": True, "id": str(alert.id), "state": alert.state}


@app.post("/api/assurance/v1/silences", dependencies=[Depends(management_auth)])
def create_silence(payload: AlertSilenceIn, db: Session = Depends(db)):
    row = AlertSilence(tenant_id=_tid(), match_labels=payload.match_labels, starts_at=payload.starts_at,
                       ends_at=payload.ends_at, reason=payload.reason, state="ACTIVE",
                       created_by=_actor_auto())
    db.add(row)
    db.commit()
    return {"ok": True, "silence_id": str(row.id)}


@app.get("/api/assurance/v1/silences", dependencies=[Depends(management_auth)])
def list_silences(db: Session = Depends(db)):
    return [{"id": str(s.id), "match_labels": s.match_labels, "starts_at": s.starts_at.isoformat(),
             "ends_at": s.ends_at.isoformat(), "state": s.state} for s in db.query(AlertSilence).all()]


@app.post("/api/assurance/v1/silences/{silence_id}/cancel", dependencies=[Depends(management_auth)])
def cancel_silence(silence_id: uuid.UUID, db: Session = Depends(db)):
    row = db.get(AlertSilence, silence_id)
    if row is None:
        _raise(AssuranceError(404, "silence not found", "NOT_FOUND"))
    row.state = "CANCELLED"
    db.commit()
    return {"ok": True}


@app.get("/api/assurance/v1/alert-routes", dependencies=[Depends(management_auth)])
def list_routes(db: Session = Depends(db)):
    return [{"id": str(r.id), "name": r.name, "match_labels": r.match_labels, "channel": r.channel,
             "fallback_route": r.fallback_route} for r in db.query(AlertRoute).all()]


@app.post("/api/assurance/v1/alert-routes", dependencies=[Depends(management_auth)])
def create_route(payload: AlertRouteIn, db: Session = Depends(db)):
    row = AlertRoute(name=payload.name, match_labels=payload.match_labels, channel=payload.channel,
                     recipients=payload.recipients, escalation_policy=payload.escalation_policy,
                     fallback_route=payload.fallback_route, is_active=True)
    db.add(row)
    db.commit()
    return {"ok": True, "id": str(row.id)}


# =====================================================================
# Incidents
# =====================================================================
@app.get("/api/assurance/v1/incidents", dependencies=[Depends(management_auth)])
def list_incidents(state: Optional[str] = None, db: Session = Depends(db)):
    q = db.query(Incident)
    if state:
        q = q.filter(Incident.state == state)
    return [{"id": str(i.id), "title": i.title, "state": i.state, "severity": i.severity,
             "is_major": i.is_major, "source": i.source,
             "detected_at": i.detected_at.isoformat(),
             "resolved_at": i.resolved_at.isoformat() if i.resolved_at else None} for i in q.all()]


@app.post("/api/assurance/v1/incidents", dependencies=[Depends(management_auth)])
def create_incident(payload: IncidentCreateIn, request: Request, db: Session = Depends(db)):
    incident = incident_service.create_incident(
        db, tenant_id=_tid(), title=payload.title, severity=payload.severity, source=payload.source,
        description=payload.description, alert_id=payload.alert_id, is_major=payload.is_major,
        actor=_actor(request))
    audit_service.audit(db, _tid(), _actor(request), "incident.created", resource_type="incident",
                        resource_id=incident.id)
    db.commit()
    return {"id": str(incident.id), "state": incident.state, "severity": incident.severity}


@app.get("/api/assurance/v1/incidents/{incident_id}", dependencies=[Depends(management_auth)])
def get_incident(incident_id: uuid.UUID, db: Session = Depends(db)):
    i = db.get(Incident, incident_id)
    if i is None:
        _raise(AssuranceError(404, "incident not found", "NOT_FOUND"))
    events = [{"event_type": e.event_type, "detail": e.detail, "occurred_at": e.occurred_at.isoformat()}
              for e in db.query(IncidentEvent).filter(IncidentEvent.incident_id == i.id).order_by(IncidentEvent.occurred_at).all()]
    return {"id": str(i.id), "title": i.title, "state": i.state, "severity": i.severity,
            "is_major": i.is_major, "description": i.description,
            "detected_at": i.detected_at.isoformat(), "resolved_at": i.resolved_at.isoformat() if i.resolved_at else None,
            "impact": incident_service.impact_summary(db, i.id).to_dict(), "events": events}


@app.post("/api/assurance/v1/incidents/{incident_id}/transition", dependencies=[Depends(management_auth)])
def transition_incident(incident_id: uuid.UUID, payload: IncidentTransitionIn, request: Request, db: Session = Depends(db)):
    incident = incident_service.transition(db, incident_id, payload.target, actor=_actor(request),
                                           detail=payload.detail)
    audit_service.audit(db, _tid(), _actor(request), f"incident.{payload.target.lower()}",
                        resource_type="incident", resource_id=incident.id)
    db.commit()
    return {"ok": True, "id": str(incident.id), "state": incident.state}


@app.post("/api/assurance/v1/incidents/{incident_id}/major", dependencies=[Depends(management_auth)])
def declare_major(incident_id: uuid.UUID, request: Request, db: Session = Depends(db)):
    incident = incident_service.declare_major(db, incident_id, actor=_actor(request))
    db.commit()
    return {"ok": True, "id": str(incident.id), "is_major": incident.is_major}


@app.post("/api/assurance/v1/incidents/{incident_id}/commanders", dependencies=[Depends(management_auth)])
def add_commander(incident_id: uuid.UUID, payload: dict, db: Session = Depends(db)):
    incident_service.add_commander(db, incident_id, payload["user_id"], payload.get("role", "COMMANDER"))
    db.commit()
    return {"ok": True}


@app.post("/api/assurance/v1/incidents/{incident_id}/responders", dependencies=[Depends(management_auth)])
def add_responder(incident_id: uuid.UUID, payload: dict, db: Session = Depends(db)):
    incident_service.add_responder(db, incident_id, payload["user_id"], payload.get("role", "RESPONDER"))
    db.commit()
    return {"ok": True}


@app.post("/api/assurance/v1/incidents/{incident_id}/alerts", dependencies=[Depends(management_auth)])
def link_alert(incident_id: uuid.UUID, payload: dict, db: Session = Depends(db)):
    incident_service.link_alert(db, incident_id, _uuid(payload["alert_id"]))
    db.commit()
    return {"ok": True}


@app.post("/api/assurance/v1/incidents/{incident_id}/tickets", dependencies=[Depends(management_auth)])
def link_ticket(incident_id: uuid.UUID, payload: dict, db: Session = Depends(db)):
    incident_service.link_ticket(db, incident_id, payload["ticket_id"],
                                 payload.get("relationship", "RELATED"))
    db.commit()
    return {"ok": True}


@app.post("/api/assurance/v1/incidents/{incident_id}/service-impact", dependencies=[Depends(management_auth)])
def add_service_impact(incident_id: uuid.UUID, payload: dict, db: Session = Depends(db)):
    incident_service.add_service_impact(db, incident_id, _uuid(payload["service_id"]),
                                        payload.get("impact_level", "PARTIAL"))
    db.commit()
    return {"ok": True}


@app.post("/api/assurance/v1/incidents/{incident_id}/impact-estimate", dependencies=[Depends(management_auth)])
def estimate_impact(incident_id: uuid.UUID, payload: ImpactEstimateIn, db: Session = Depends(db)):
    row = incident_service.estimate_customer_impact(db, incident_id, impact_kind=payload.impact_kind,
                                                    estimated_subscribers=payload.estimated_subscribers,
                                                    detail=payload.detail, impact_ref=payload.impact_ref)
    db.commit()
    return {"ok": True, "impact_id": str(row.id), "estimated": True}


@app.post("/api/assurance/v1/incidents/{incident_id}/impact-confirm", dependencies=[Depends(management_auth)])
def confirm_impact(incident_id: uuid.UUID, payload: ImpactConfirmIn, db: Session = Depends(db)):
    row = incident_service.confirm_customer_impact(db, incident_id, impact_kind=payload.impact_kind,
                                                   confirmed_subscribers=payload.confirmed_subscribers,
                                                   impact_ref=payload.impact_ref)
    db.commit()
    return {"ok": True, "impact_id": str(row.id), "estimated": False,
            "confirmed_subscribers": row.confirmed_subscribers}


@app.post("/api/assurance/v1/incidents/{incident_id}/communications", dependencies=[Depends(management_auth)])
def add_communication(incident_id: uuid.UUID, payload: CommunicationIn, db: Session = Depends(db)):
    row = incident_service.add_communication(db, incident_id, audience=payload.audience,
                                             message=payload.message, channel=payload.channel)
    db.commit()
    return {"ok": True, "communication_id": str(row.id)}


@app.post("/api/assurance/v1/incidents/{incident_id}/actions", dependencies=[Depends(management_auth)])
def create_action(incident_id: uuid.UUID, payload: IncidentActionIn, db: Session = Depends(db)):
    row = incident_service.create_action(db, incident_id, action_type=payload.action_type,
                                         description=payload.description, assigned_to=payload.assigned_to)
    db.commit()
    return {"ok": True, "action_id": str(row.id)}


@app.post("/api/assurance/v1/incidents/{incident_id}/require-postmortem", dependencies=[Depends(management_auth)])
def require_postmortem(incident_id: uuid.UUID, request: Request, db: Session = Depends(db)):
    incident = incident_service.require_postmortem(db, incident_id, actor=_actor(request))
    db.commit()
    return {"ok": True, "id": str(incident.id), "state": incident.state}


# =====================================================================
# Root cause
# =====================================================================
@app.post("/api/assurance/v1/incidents/{incident_id}/root-causes", dependencies=[Depends(management_auth)])
def create_hypothesis(incident_id: uuid.UUID, payload: HypothesisIn, request: Request, db: Session = Depends(db)):
    row = incident_service.create_hypothesis(db, incident_id, hypothesis=payload.hypothesis,
                                             confidence=payload.confidence,
                                             created_by=_actor(request),
                                             is_ai_suggestion=payload.is_ai_suggestion)
    db.commit()
    return {"ok": True, "hypothesis_id": str(row.id), "state": row.state}


@app.post("/api/assurance/v1/root-causes/{hypothesis_id}/evidence", dependencies=[Depends(management_auth)])
def add_evidence(hypothesis_id: uuid.UUID, payload: EvidenceIn, db: Session = Depends(db)):
    row = incident_service.add_evidence(db, hypothesis_id, evidence_type=payload.evidence_type,
                                        evidence_ref=payload.evidence_ref, supports=payload.supports,
                                        detail=payload.detail)
    db.commit()
    return {"ok": True, "evidence_id": str(row.id), "supports": row.supports}


@app.post("/api/assurance/v1/root-causes/{hypothesis_id}/transition", dependencies=[Depends(management_auth)])
def transition_hypothesis(hypothesis_id: uuid.UUID, payload: dict, db: Session = Depends(db)):
    row = incident_service.transition_hypothesis(db, hypothesis_id, payload["target"])
    db.commit()
    return {"ok": True, "hypothesis_id": str(row.id), "state": row.state}


@app.post("/api/assurance/v1/root-causes/{hypothesis_id}/confirm", dependencies=[Depends(management_auth)])
def confirm_root_cause(hypothesis_id: uuid.UUID, payload: ConfirmRootCauseIn, db: Session = Depends(db)):
    row = incident_service.confirm_root_cause(db, hypothesis_id, confirmed_by=payload.confirmed_by)
    db.commit()
    return {"ok": True, "hypothesis_id": str(row.id), "state": row.state}


@app.get("/api/assurance/v1/incidents/{incident_id}/root-causes", dependencies=[Depends(management_auth)])
def list_hypotheses(incident_id: uuid.UUID, db: Session = Depends(db)):
    rows = db.query(RootCauseHypothesis).filter(RootCauseHypothesis.incident_id == incident_id).all()
    return [{"id": str(h.id), "state": h.state, "hypothesis": h.hypothesis,
             "confidence": h.confidence, "supporting_evidence": h.supporting_evidence,
             "contradicting_evidence": h.contradicting_evidence, "is_ai_suggestion": h.is_ai_suggestion}
            for h in rows]


# =====================================================================
# Postmortems
# =====================================================================
@app.post("/api/assurance/v1/postmortems", dependencies=[Depends(management_auth)])
def create_postmortem(payload: PostmortemCreateIn, request: Request, db: Session = Depends(db)):
    # payload must carry incident_id
    if not hasattr(payload, "incident_id"):
        _raise(AssuranceError(422, "incident_id required", "VALIDATION"))
    pm = incident_service.create_postmortem(db, payload.incident_id, tenant_id=_tid(),
                                            summary=payload.summary, root_cause=payload.root_cause,
                                            actor=_actor(request))
    db.commit()
    return {"ok": True, "postmortem_id": str(pm.id), "state": pm.state}


@app.post("/api/assurance/v1/postmortems/{postmortem_id}/actions", dependencies=[Depends(management_auth)])
def add_postmortem_action(postmortem_id: uuid.UUID, payload: PostmortemActionIn, db: Session = Depends(db)):
    row = incident_service.add_postmortem_action(db, postmortem_id, title=payload.title,
                                                 owner=payload.owner, due_at=payload.due_at)
    db.commit()
    return {"ok": True, "action_item_id": str(row.id), "state": row.state}


@app.get("/api/assurance/v1/postmortems", dependencies=[Depends(management_auth)])
def list_postmortems(db: Session = Depends(db)):
    return [{"id": str(p.id), "incident_id": str(p.incident_id), "state": p.state} for p in db.query(Postmortem).all()]


# =====================================================================
# KPIs
# =====================================================================
@app.get("/api/assurance/v1/kpis", dependencies=[Depends(management_auth)])
def list_kpis(db: Session = Depends(db)):
    return kpi_service.list_kpis(db, _tid())


@app.post("/api/assurance/v1/kpis", dependencies=[Depends(management_auth)])
def create_kpi(payload: KpiDefinitionIn, db: Session = Depends(db)):
    row = kpi_service.create_kpi(db, payload.model_dump())
    db.commit()
    return {"ok": True, "id": str(row.id), "code": row.code}


@app.post("/api/assurance/v1/kpi-measurements", dependencies=[Depends(management_auth)])
def record_kpi(payload: KpiMeasurementIn, db: Session = Depends(db)):
    row = kpi_service.record_measurement(db, _tid(), payload.kpi_code, period_key=payload.period_key,
                                         value=payload.value, quality=payload.quality,
                                         dimensions=payload.dimensions, measured_at=payload.measured_at)
    db.commit()
    return {"ok": True, "measurement_id": str(row.id)}


@app.post("/api/assurance/v1/kpi-targets", dependencies=[Depends(management_auth)])
def set_kpi_target(payload: KpiTargetIn, db: Session = Depends(db)):
    row = kpi_service.set_target(db, _tid(), payload.kpi_code, target=payload.target,
                                 direction=payload.direction, target_key=payload.target_key)
    db.commit()
    return {"ok": True, "target_id": str(row.id)}


# =====================================================================
# Synthetic checks
# =====================================================================
@app.get("/api/assurance/v1/synthetic", dependencies=[Depends(management_auth)])
def list_synthetic(db: Session = Depends(db)):
    return [{"id": str(c.id), "code": c.code, "kind": c.kind, "target": c.target,
             "is_active": c.is_active} for c in synthetic_service.list_checks(db, _tid())]


@app.post("/api/assurance/v1/synthetic", dependencies=[Depends(management_auth)])
def create_synthetic(payload: SyntheticCheckIn, db: Session = Depends(db)):
    row = synthetic_service.create_check(db, tenant_id=_tid(), code=payload.code, kind=payload.kind,
                                         target=payload.target, frequency_seconds=payload.frequency_seconds,
                                         timeout_seconds=payload.timeout_seconds, tags=payload.tags)
    db.commit()
    return {"ok": True, "id": str(row.id), "code": row.code}


@app.post("/api/assurance/v1/synthetic/results", dependencies=[Depends(management_auth)])
def record_synthetic(payload: SyntheticResultIn, db: Session = Depends(db)):
    row = synthetic_service.record_result(db, tenant_id=_tid(), check_code=payload.check_code,
                                          result=payload.result, latency_ms=payload.latency_ms,
                                          detail=payload.detail)
    db.commit()
    return {"ok": True, "result_id": str(row.id)}


# =====================================================================
# Dashboards & reports
# =====================================================================
@app.get("/api/assurance/v1/dashboards/tenant", dependencies=[Depends(management_auth)])
def tenant_dashboard(hours: int = 24, db: Session = Depends(db)):
    ctx = require_tenant()
    return report_service.tenant_dashboard(db, ctx.tenant_id, hours=hours)


@app.get("/api/assurance/v1/reports/incidents", dependencies=[Depends(management_auth)])
def incident_report(days: int = 7, db: Session = Depends(db)):
    ctx = require_tenant()
    return report_service.incident_report(db, ctx.tenant_id, days=days)


@app.get("/api/assurance/v1/reports/slo-budgets", dependencies=[Depends(management_auth)])
def slo_budget_report(db: Session = Depends(db)):
    ctx = require_tenant()
    return report_service.slo_budget_report(db, ctx.tenant_id)


@app.get("/api/assurance/v1/dashboards/platform", dependencies=[Depends(management_auth)])
def platform_dashboard(hours: int = 24, db: Session = Depends(db)):
    require_platform_aggregate()
    return report_service.platform_aggregate(db, hours=hours)


@app.get("/api/assurance/v1/reports/aggregate", dependencies=[Depends(management_auth)])
def platform_aggregate_report(hours: int = 24, db: Session = Depends(db)):
    require_platform_aggregate()
    return report_service.platform_aggregate(db, hours=hours)


@app.get("/api/assurance/v1/audit-log", dependencies=[Depends(management_auth)])
def audit_log(limit: int = 100, db: Session = Depends(db)):
    from .models import AuditLog
    rows = db.query(AuditLog).order_by(AuditLog.occurred_at.desc()).limit(limit).all()
    return [{"id": str(r.id), "actor": r.actor, "action": r.action, "resource_type": r.resource_type,
             "resource_id": r.resource_id, "tenant_id": str(r.tenant_id) if r.tenant_id else None,
             "occurred_at": r.occurred_at.isoformat()} for r in rows]


# =====================================================================
# Metadata: metric registry
# =====================================================================
@app.get("/api/assurance/v1/metric-registry", dependencies=[Depends(management_auth)])
def metric_registry(db: Session = Depends(db)):
    return [{"id": str(m.id), "name": m.name, "metric_type": m.metric_type, "safe_labels": m.safe_labels}
            for m in db.query(MetricRegistry).all()]


def _actor_auto():
    ctx = get_context()
    return ctx.user_id if ctx else None
