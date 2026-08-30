"""SIEM Service API (Master Spec Batch 1: security & compliance)."""
import json
import uuid
from datetime import datetime, timezone
from os import getenv

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from . import models, schemas
from .database import SessionLocal
from .routing import enforce_scope, record_audit, require_tenant_id
from .security import _required_permission, get_auth_context, require_permission
from .context import TenantContext
from .services import (CaseService, ComplianceOpsService, ConsentService, DsarService,
                       EventService, LiService, PolicyService, RetentionService,
                       VulnerabilityService)


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


app = FastAPI(title="SIEM Service", version="0.2.0", description="Security event, compliance, case, and audit foundation")


@app.get("/health")
def health():
    return {"status": "ok", "service": getenv("SERVICE_NAME", "siem-service")}


@app.get("/status")
def status():
    from . import events as ev
    return {"service": "siem", "phase": "batch1", "published_events": sorted(ev.PUBLISHED_TOPOLOGY)}


# ---------------------------------------------------------------------------
# Event ingestion (features 407, 408, 448)
# ---------------------------------------------------------------------------
@app.post("/api/siem/v1/internal/ingest/events")
def internal_ingest(payload: dict, request: Request,
                    ctx: TenantContext = Depends(_auth("events.ingest"))):
    """High-volume bulk ingest from agents/collectors (internal API key)."""
    items = payload.get("events") or []
    if not items:
        raise HTTPException(status_code=422, detail="No events supplied")
    tid = payload.get("tenant_id")
    if not tid:
        raise HTTPException(status_code=422, detail="tenant_id required")
    ingest_ctx = TenantContext(user_id="system:internal", role="SECURITY_OPS",
                               tenant_id=uuid.UUID(str(tid)), permissions=ctx.permissions,
                               is_platform_aggregate=False)
    db = SessionLocal()
    try:
        rows = EventService.ingest(db, ingest_ctx, items, created_by="internal")
        return {"ingested": len(rows), "ids": [str(r.id) for r in rows]}
    finally:
        db.close()


@app.post("/api/siem/v1/security-events", status_code=201)
def create_event(body: schemas.SecurityEventIn, request: Request,
                 db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("events.ingest"))):
    require_tenant_id(ctx)
    rows = EventService.ingest(db, ctx, [body.model_dump()])
    return schemas.SecurityEventOut.model_validate(rows[0])


@app.get("/api/siem/v1/security-events")
def list_events(request: Request,
                event_type: str | None = None, category: str | None = None,
                severity: str | None = None, source_ip: str | None = None,
                q: str | None = None, limit: int = Query(100, le=1000), offset: int = 0,
                db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("events.view"))):
    query = enforce_scope(db.query(models.SecurityEvent), models.SecurityEvent, ctx)
    if event_type:
        query = query.filter(models.SecurityEvent.event_type == event_type)
    if category:
        query = query.filter(models.SecurityEvent.category == category)
    if severity:
        query = query.filter(models.SecurityEvent.severity == severity)
    if source_ip:
        query = query.filter(models.SecurityEvent.source_ip == source_ip)
    if q:
        query = query.filter(models.SecurityEvent.event_type.ilike(f"%{q}%"))
    total = query.count()
    rows = query.order_by(models.SecurityEvent.event_time.desc()).offset(offset).limit(limit).all()
    return {"total": total, "items": [schemas.SecurityEventOut.model_validate(r) for r in rows]}


@app.get("/api/siem/v1/security-events/{event_id}")
def get_event(event_id: uuid.UUID, request: Request,
              db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("events.view"))):
    row = enforce_scope(db.query(models.SecurityEvent).filter(
        models.SecurityEvent.id == event_id), models.SecurityEvent, ctx).first()
    if not row:
        raise HTTPException(status_code=404, detail="Event not found")
    return schemas.SecurityEventOut.model_validate(row)


@app.get("/api/siem/v1/security-events/{event_id}/evidence")
def get_evidence(event_id: uuid.UUID, request: Request,
                 db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("evidence.view"))):
    row = enforce_scope(db.query(models.SecurityEvent).filter(
        models.SecurityEvent.id == event_id), models.SecurityEvent, ctx).first()
    if not row:
        raise HTTPException(status_code=404, detail="Event not found")
    blocks = db.query(models.EvidenceBlock).filter(models.EvidenceBlock.event_id == event_id).all()
    return {"event_id": str(event_id), "digest": row.digest, "prev_hash": row.prev_hash,
            "verified": _verify(row, blocks), "blocks": [schemas.EvidenceOut.model_validate(b) for b in blocks]}


def _verify(event: models.SecurityEvent, blocks: list[models.EvidenceBlock]) -> bool:
    import json as _json
    from .crypto import chain_hash
    if not blocks:
        return False
    chain_input = {
        "tenant": str(event.tenant_id), "event_type": event.event_type,
        "category": event.category, "severity": event.severity,
        "source_ip": event.source_ip, "actor": event.actor, "target": event.target,
        "payload": event.payload,
        "event_time": event.event_time.isoformat() if event.event_time else None,
    }
    canonical = _json.dumps(chain_input, sort_keys=True, default=str)
    return chain_hash(event.prev_hash, canonical) == event.digest


@app.post("/api/siem/v1/security-events/export")
def export_events(request: Request, payload: dict = None,
                  db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("events.export"))):
    """Log export for audit (feature 410) as JSON/NDJSON."""
    payload = payload or {}
    query = enforce_scope(db.query(models.SecurityEvent), models.SecurityEvent, ctx)
    fmt = payload.get("format", "ndjson")
    rows = query.order_by(models.SecurityEvent.event_time).limit(10000).all()
    record_audit(db, ctx, "events.export", "SecurityEvent", outcome="SUCCESS",
                 detail={"count": len(rows), "format": fmt})
    db.commit()
    data = [schemas.SecurityEventOut.model_validate(r).model_dump(mode="json") for r in rows]
    if fmt == "ndjson":
        body = "\n".join(json.dumps(d) for d in data)
        return Response(content=body, media_type="application/x-ndjson",
                        headers={"Content-Disposition": 'attachment; filename="events.ndjson"'})
    return Response(content=json.dumps(data), media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="events.json"'})


# ---------------------------------------------------------------------------
# Compliance policies + violations (features 401, 426, 441, 442)
# ---------------------------------------------------------------------------
@app.post("/api/siem/v1/policies", status_code=201)
def create_policy(body: schemas.PolicyIn, request: Request,
                  db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("policies.manage"))):
    p = PolicyService.create(db, ctx, body.model_dump())
    return schemas.PolicyOut.model_validate(p)


@app.get("/api/siem/v1/policies")
def list_policies(request: Request, category: str | None = None,
                  db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("policies.manage"))):
    q = enforce_scope(db.query(models.CompliancePolicy), models.CompliancePolicy, ctx)
    if category:
        q = q.filter(models.CompliancePolicy.category == category)
    return [schemas.PolicyOut.model_validate(p) for p in q.order_by(models.CompliancePolicy.name).all()]


@app.post("/api/siem/v1/policies/{policy_id}/evaluate")
def evaluate_policy(policy_id: uuid.UUID, request: Request,
                    db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("policies.manage"))):
    p = enforce_scope(db.query(models.CompliancePolicy).filter(
        models.CompliancePolicy.id == policy_id), models.CompliancePolicy, ctx).first()
    if not p:
        raise HTTPException(status_code=404, detail="Policy not found")
    events = enforce_scope(db.query(models.SecurityEvent), models.SecurityEvent, ctx).limit(50).all()
    matched = 0
    for e in events:
        matched += len(PolicyService.evaluate(db, ctx, e, policy_id=p.id))
    db.commit()
    return {"policy_id": str(policy_id), "matched": matched}


@app.get("/api/siem/v1/violations")
def list_violations(request: Request, status: str | None = None,
                    db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("violations.view"))):
    q = enforce_scope(db.query(models.PolicyViolation), models.PolicyViolation, ctx)
    if status:
        q = q.filter(models.PolicyViolation.status == status)
    return [schemas.ViolationOut.model_validate(v) for v in
            q.order_by(models.PolicyViolation.detected_at.desc()).limit(500).all()]


@app.post("/api/siem/v1/violations/{violation_id}/resolve")
def resolve_violation(violation_id: uuid.UUID, request: Request,
                      db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("violations.manage"))):
    v = PolicyService.resolve(db, ctx, violation_id)
    return schemas.ViolationOut.model_validate(v)


# ---------------------------------------------------------------------------
# Retention (features 404, 405, 406, 1334)
# ---------------------------------------------------------------------------
@app.post("/api/siem/v1/retention-policies", status_code=201)
def set_retention(body: schemas.RetentionIn, request: Request,
                  db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("retention.manage"))):
    r = RetentionService.set_policy(db, ctx, body.model_dump())
    return {"id": str(r.id), "data_class": r.data_class, "retention_days": r.retention_days,
            "action": r.action}


@app.get("/api/siem/v1/retention-policies")
def list_retention(request: Request, db: Session = Depends(_db),
                   ctx: TenantContext = Depends(_auth("retention.manage"))):
    q = enforce_scope(db.query(models.RetentionPolicy), models.RetentionPolicy, ctx)
    return [{"id": str(r.id), "data_class": r.data_class, "retention_days": r.retention_days,
             "action": r.action, "enabled": r.enabled, "regulatory_ref": r.regulatory_ref}
            for r in q.all()]


@app.post("/api/siem/v1/retention/run")
def run_retention(request: Request, payload: dict = None,
                  db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("retention.manage"))):
    data_class = (payload or {}).get("data_class")
    return RetentionService.apply(db, ctx, data_class)


# ---------------------------------------------------------------------------
# Consent + DSAR (features 421, 422, 423, 1240)
# ---------------------------------------------------------------------------
@app.post("/api/siem/v1/consent", status_code=201)
def set_consent(body: schemas.ConsentIn, request: Request,
                db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("consent.manage"))):
    c = ConsentService.set(db, ctx, body.model_dump())
    return schemas.ConsentOut.model_validate(c)


@app.get("/api/siem/v1/consent")
def list_consent(request: Request, subscriber_id: str | None = None,
                 db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("consent.view"))):
    q = enforce_scope(db.query(models.ConsentRecord), models.ConsentRecord, ctx)
    if subscriber_id:
        q = q.filter(models.ConsentRecord.subscriber_id == subscriber_id)
    return [schemas.ConsentOut.model_validate(c) for c in q.all()]


@app.post("/api/siem/v1/data-requests", status_code=201)
def create_data_request(body: schemas.DataRequestIn, request: Request,
                        db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("dsar.manage"))):
    r = DsarService.create(db, ctx, body.model_dump())
    return schemas.DataRequestOut.model_validate(r)


@app.get("/api/siem/v1/data-requests")
def list_data_requests(request: Request, db: Session = Depends(_db),
                       ctx: TenantContext = Depends(_auth("dsar.manage"))):
    q = enforce_scope(db.query(models.DataAccessRequest), models.DataAccessRequest, ctx)
    return [schemas.DataRequestOut.model_validate(r) for r in q.order_by(
        models.DataAccessRequest.created_at.desc()).limit(200).all()]


@app.post("/api/siem/v1/data-requests/{req_id}/fulfill")
def fulfill_data_request(req_id: uuid.UUID, request: Request,
                         db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("dsar.manage"))):
    return schemas.DataRequestOut.model_validate(DsarService.fulfill(db, ctx, req_id))


@app.post("/api/siem/v1/data-requests/{req_id}/erase")
def erase_data_request(req_id: uuid.UUID, request: Request,
                       db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("dsar.manage"))):
    return schemas.DataRequestOut.model_validate(DsarService.erase(db, ctx, req_id))


# ---------------------------------------------------------------------------
# Security cases + SOC workflow (features 1414, 1415, 1471, 1472, 1473, 1474)
# ---------------------------------------------------------------------------
@app.post("/api/siem/v1/cases", status_code=201)
def create_case(body: schemas.CaseIn, request: Request,
                db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("cases.manage"))):
    c = CaseService.create(db, ctx, body.model_dump())
    return schemas.CaseOut.model_validate(c)


@app.get("/api/siem/v1/cases")
def list_cases(request: Request, status: str | None = None, severity: str | None = None,
               db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("cases.view"))):
    q = enforce_scope(db.query(models.SecurityCase), models.SecurityCase, ctx)
    if status:
        q = q.filter(models.SecurityCase.status == status)
    if severity:
        q = q.filter(models.SecurityCase.severity == severity)
    return [schemas.CaseOut.model_validate(c) for c in
            q.order_by(models.SecurityCase.priority_score.desc()).limit(500).all()]


@app.get("/api/siem/v1/cases/{case_id}")
def get_case(case_id: uuid.UUID, request: Request,
             db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("cases.view"))):
    c = enforce_scope(db.query(models.SecurityCase).filter(
        models.SecurityCase.id == case_id), models.SecurityCase, ctx).first()
    if not c:
        raise HTTPException(status_code=404, detail="Case not found")
    return schemas.CaseOut.model_validate(c)


@app.get("/api/siem/v1/cases/{case_id}/timeline")
def case_timeline(case_id: uuid.UUID, request: Request,
                  db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("cases.view"))):
    c = enforce_scope(db.query(models.SecurityCase).filter(
        models.SecurityCase.id == case_id), models.SecurityCase, ctx).first()
    if not c:
        raise HTTPException(status_code=404, detail="Case not found")
    evts = db.query(models.CaseEvent).filter(models.CaseEvent.case_id == case_id) \
        .order_by(models.CaseEvent.created_at).all()
    return [{"id": str(e.id), "from": e.from_state, "to": e.to_state,
             "transition": e.transition, "note": e.note, "actor": e.actor,
             "created_at": e.created_at} for e in evts]


@app.post("/api/siem/v1/cases/{case_id}/transition")
def transition_case(case_id: uuid.UUID, body: schemas.TransitionIn, request: Request,
                    db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("cases.manage"))):
    try:
        c = CaseService.transition(db, ctx, case_id, body.transition, body.note)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return schemas.CaseOut.model_validate(c)


@app.post("/api/siem/v1/cases/{case_id}/escalate")
def escalate_case(case_id: uuid.UUID, request: Request,
                  db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("cases.escalate"))):
    try:
        c = CaseService.escalate(db, ctx, case_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return schemas.CaseOut.model_validate(c)


@app.post("/api/siem/v1/cases/{case_id}/impact")
def assess_impact(case_id: uuid.UUID, request: Request,
                  db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("cases.manage"))):
    try:
        c = CaseService.assess_impact(db, ctx, case_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return schemas.CaseOut.model_validate(c)


@app.post("/api/siem/v1/cases/{case_id}/notify")
def notify_breach(case_id: uuid.UUID, body: schemas.BreachNotifyIn, request: Request,
                  db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("cases.manage"))):
    try:
        n = CaseService.notify(db, ctx, case_id, body.model_dump())
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return n


@app.get("/api/siem/v1/breach/notifications")
def list_notifications(request: Request, db: Session = Depends(_db),
                       ctx: TenantContext = Depends(_auth("cases.view"))):
    q = enforce_scope(db.query(models.SecurityCase).filter(
        models.SecurityCase.notification_tracked.is_(True)), models.SecurityCase, ctx)
    return [{"case_id": str(c.id), "ref_id": c.ref_id, "title": c.title,
             "notification_tracked": c.notification_tracked} for c in q.limit(200).all()]


# ---------------------------------------------------------------------------
# Vulnerabilities (features 1173, 1174, 1175)
# ---------------------------------------------------------------------------
@app.post("/api/siem/v1/vulnerabilities", status_code=201)
def ingest_vuln(body: schemas.VulnerabilityIn, request: Request,
                db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("vuln.manage"))):
    v = VulnerabilityService.ingest(db, ctx, body.model_dump())
    return {"id": str(v.id), "target": v.target, "severity": v.severity, "cve": v.cve}


@app.get("/api/siem/v1/vulnerabilities")
def list_vulns(request: Request, status: str | None = None,
               db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("vuln.manage"))):
    q = enforce_scope(db.query(models.Vulnerability), models.Vulnerability, ctx)
    if status:
        q = q.filter(models.Vulnerability.status == status)
    return [{"id": str(v.id), "target": v.target, "scanner": v.scanner, "severity": v.severity,
             "cve": v.cve, "status": v.status, "discovered_at": v.discovered_at} for v in
            q.order_by(models.Vulnerability.discovered_at.desc()).limit(500).all()]


@app.post("/api/siem/v1/vulnerabilities/{vuln_id}/remediate")
def remediate_vuln(vuln_id: uuid.UUID, request: Request,
                   db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("vuln.manage"))):
    try:
        v = VulnerabilityService.remediate(db, ctx, vuln_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"id": str(v.id), "status": v.status}


# ---------------------------------------------------------------------------
# Lawful interception (features 411-416)
# ---------------------------------------------------------------------------
@app.post("/api/siem/v1/li/requests", status_code=201)
def li_request(body: schemas.LIRequestIn, request: Request,
               db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("events.ingest"))):
    r = LiService.request(db, ctx, body.model_dump())
    return {"id": str(r.id), "status": r.status}


@app.post("/api/siem/v1/li/requests/{req_id}/decide")
def li_decide(req_id: uuid.UUID, body: schemas.LIDecideIn, request: Request,
              db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("li.approve"))):
    try:
        r = LiService.decide(db, ctx, req_id, body.decision, body.approver_note)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"id": str(r.id), "status": r.status}


@app.get("/api/siem/v1/li/requests")
def list_li(request: Request, db: Session = Depends(_db),
            ctx: TenantContext = Depends(_auth("events.view"))):
    q = enforce_scope(db.query(models.LIRequest), models.LIRequest, ctx)
    return [{"id": str(r.id), "target_subscriber": r.target_subscriber, "status": r.status,
             "requested_at": r.requested_at, "decided_at": r.decided_at} for r in q.all()]


# ---------------------------------------------------------------------------
# Audit trail (features 420, 438, 439, 440, 1163)
# ---------------------------------------------------------------------------
@app.get("/api/siem/v1/audit-log")
def list_audit(request: Request, action: str | None = None, actor: str | None = None,
               limit: int = Query(200, le=1000), db: Session = Depends(_db),
               ctx: TenantContext = Depends(_auth("audit.view"))):
    q = enforce_scope(db.query(models.AuditLog), models.AuditLog, ctx)
    if action:
        q = q.filter(models.AuditLog.action == action)
    if actor:
        q = q.filter(models.AuditLog.actor == actor)
    rows = q.order_by(models.AuditLog.created_at.desc()).limit(limit).all()
    return [{"id": str(r.id), "actor": r.actor, "action": r.action, "resource": r.resource,
             "resource_id": r.resource_id, "outcome": r.outcome, "detail": r.detail,
             "created_at": r.created_at} for r in rows]


@app.post("/api/siem/v1/audit-log/export")
def export_audit(request: Request, payload: dict = None,
                 db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("audit.export"))):
    q = enforce_scope(db.query(models.AuditLog), models.AuditLog, ctx)
    rows = q.order_by(models.AuditLog.created_at).limit(10000).all()
    data = [{"id": str(r.id), "actor": r.actor, "action": r.action, "resource": r.resource,
             "resource_id": r.resource_id, "outcome": r.outcome, "detail": r.detail,
             "created_at": r.created_at.isoformat()} for r in rows]
    return Response(content=json.dumps(data, default=str), media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="audit-log.json"'})


# ---------------------------------------------------------------------------
# Dashboard + regulatory (features 425, 428, 1234, 1475)
# ---------------------------------------------------------------------------
@app.get("/api/siem/v1/dashboard/summary")
def dashboard_summary(request: Request, db: Session = Depends(_db),
                      ctx: TenantContext = Depends(_auth("dashboard.view"))):
    events_q = enforce_scope(db.query(models.SecurityEvent), models.SecurityEvent, ctx)
    cases_q = enforce_scope(db.query(models.SecurityCase), models.SecurityCase, ctx)
    vulns_q = enforce_scope(db.query(models.Vulnerability), models.Vulnerability, ctx)
    violations_q = enforce_scope(db.query(models.PolicyViolation), models.PolicyViolation, ctx)
    return {
        "total_events": events_q.count(),
        "open_cases": cases_q.filter(models.SecurityCase.status == "OPEN").count(),
        "critical_severity_events": events_q.filter(
            models.SecurityEvent.severity == "CRITICAL").count(),
        "open_vulnerabilities": vulns_q.filter(models.Vulnerability.status == "OPEN").count(),
        "open_violations": violations_q.filter(models.PolicyViolation.status == "OPEN").count(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/api/siem/v1/regulatory/reports")
def regulatory_report(request: Request, payload: dict = None,
                      db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("reports.export"))):
    """Generate a TRAI/DoT style compliance report (features 401, 428, 1335, 1475)."""
    payload = payload or {}
    report_type = payload.get("type", "compliance_summary")
    data = dashboard_summary(request, db, ctx)
    report = {
        "type": report_type,
        "regulator": payload.get("regulator", "TRAI"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_by": ctx.user_id,
        "summary": data,
    }
    record_audit(db, ctx, "regulatory.report", "Report", outcome="SUCCESS",
                 detail={"type": report_type})
    db.commit()
    return report


# ---------------------------------------------------------------------------
# Compliance ops: circle mapping, geo blocking, playbooks, adaptive MFA
# (features 403, 1164, 1236, 1370)
# ---------------------------------------------------------------------------
@app.post("/api/siem/v1/compliance/circles", status_code=201)
def create_circle(body: dict, request: Request,
                  db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("policies.manage"))):
    require_tenant_id(ctx)
    row = ComplianceOpsService.create_circle(db, ctx, body)
    return {"id": str(row.id), "circle_name": row.circle_name, "state_codes": row.state_codes}


@app.get("/api/siem/v1/compliance/circles")
def list_circles(request: Request, db: Session = Depends(_db),
                 ctx: TenantContext = Depends(_auth("violations.view"))):
    q = enforce_scope(db.query(models.CircleRegion), models.CircleRegion, ctx)
    return [{"id": str(r.id), "operator": r.operator, "circle_name": r.circle_name,
             "state_codes": r.state_codes, "status": r.status} for r in q.all()]


@app.post("/api/siem/v1/compliance/geo-block", status_code=201)
def create_geo_rule(body: dict, request: Request,
                    db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("policies.manage"))):
    require_tenant_id(ctx)
    row = ComplianceOpsService.create_geo_rule(db, ctx, body)
    return {"id": str(row.id), "service": row.service, "region_code": row.region_code,
            "action": row.action}


@app.post("/api/siem/v1/compliance/geo-block/evaluate")
def evaluate_geo(body: dict, request: Request,
                 db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("violations.view"))):
    require_tenant_id(ctx)
    return ComplianceOpsService.evaluate_geo(db, ctx, body.get("service"),
                                             body.get("region_code"))


@app.post("/api/siem/v1/threat/playbooks", status_code=201)
def create_playbook(body: dict, request: Request,
                    db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("cases.manage"))):
    require_tenant_id(ctx)
    p = ComplianceOpsService.create_playbook(db, ctx, body)
    return {"id": str(p.id), "name": p.name, "steps": len(p.steps or []), "status": p.status}


@app.get("/api/siem/v1/threat/playbooks")
def list_playbooks(request: Request, db: Session = Depends(_db),
                   ctx: TenantContext = Depends(_auth("cases.manage"))):
    q = enforce_scope(db.query(models.ThreatPlaybook), models.ThreatPlaybook, ctx)
    return [{"id": str(p.id), "name": p.name, "tactic": p.tactic, "status": p.status,
             "executions": p.executions} for p in q.all()]


@app.post("/api/siem/v1/threat/playbooks/{playbook_id}/execute")
def execute_playbook(playbook_id: uuid.UUID, request: Request,
                     db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("cases.manage"))):
    try:
        p = ComplianceOpsService.execute_playbook(db, ctx, playbook_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"id": str(p.id), "name": p.name, "executions": p.executions}


@app.post("/api/siem/v1/security/mfa-rules", status_code=201)
def create_mfa_rule(body: dict, request: Request,
                    db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("events.ingest"))):
    require_tenant_id(ctx)
    r = ComplianceOpsService.create_mfa_rule(db, ctx, body)
    return {"id": str(r.id), "name": r.name, "trigger_action": r.trigger_action, "enabled": r.enabled}


@app.post("/api/siem/v1/security/mfa-rules/evaluate")
def evaluate_mfa(body: dict, request: Request,
                 db: Session = Depends(_db), ctx: TenantContext = Depends(_auth("events.ingest"))):
    require_tenant_id(ctx)
    return ComplianceOpsService.evaluate_mfa(db, ctx, body.get("context", {}))
