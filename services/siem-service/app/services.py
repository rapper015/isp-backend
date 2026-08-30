"""SIEM domain services: event ingestion + tamper-evidence, compliance policies,
retention, consent/DSAR, security cases, audit, LI, vulnerabilities."""
import json
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from . import events, models
from .context import TenantContext
from .crypto import chain_hash, mask_pii, sha256
from .enums import (CASE_FLOW, SEVERITY_WEIGHT, CaseStatus, CaseTransition,
                    DataClass, RetentionAction)
from .routing import record_audit


def _utcnow():
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Event ingestion (features 407 Central Log Repository, 408 Tamper Proof Logs,
# 448 High Volume Logging, 417 encryption, 418 masking)
# ---------------------------------------------------------------------------
class EventService:
    SENSITIVE_FIELDS = {"password", "token", "secret", "api_key", "authorization",
                        "cookie", "ssn", "pan", "content"}

    @staticmethod
    def _mask_payload(payload: dict) -> dict:
        out = {}
        for k, v in payload.items():
            if k in EventService.SENSITIVE_FIELDS:
                out[k] = "***REDACTED***"
            elif isinstance(v, dict):
                out[k] = EventService._mask_payload(v)
            elif isinstance(v, str):
                out[k] = mask_pii(v)
            else:
                out[k] = v
        return out

    @staticmethod
    def ingest(session: Session, ctx: TenantContext, items: list[dict],
               created_by: str | None = None) -> list[models.SecurityEvent]:
        tenant_id = ctx.require_tenant() if ctx.tenant_id else None
        events_out: list[models.SecurityEvent] = []
        last_hash = session.query(func.max(models.SecurityEvent.block_index)) \
            .filter(models.SecurityEvent.tenant_id == tenant_id).scalar()
        block_index = (last_hash or 0)
        prev_hash = None
        if block_index:
            last_row = session.query(models.SecurityEvent) \
                .filter(models.SecurityEvent.tenant_id == tenant_id,
                        models.SecurityEvent.block_index == block_index).first()
            prev_hash = last_row.digest if last_row else None

        for item in items:
            payload = item.get("payload") or {}
            masked = EventService._mask_payload(payload)
            event_time = item.get("event_time") or _utcnow()
            if isinstance(event_time, str):
                event_time = datetime.fromisoformat(event_time.replace("Z", "+00:00"))
            # Normalize to naive UTC so read-back from storage hashes identically.
            event_time = event_time.astimezone(timezone.utc).replace(tzinfo=None)
            block_index += 1
            chain_input = {
                "tenant": str(tenant_id), "event_type": item.get("event_type"),
                "category": item.get("category", "OTHER"),
                "severity": item.get("severity", "MEDIUM"),
                "source_ip": item.get("source_ip"), "actor": item.get("actor"),
                "target": item.get("target"), "payload": payload, "event_time": event_time.isoformat(),
            }
            canonical = json.dumps(chain_input, sort_keys=True, default=str)
            digest = chain_hash(prev_hash, canonical)
            row = models.SecurityEvent(
                tenant_id=tenant_id,
                event_type=item.get("event_type", "generic"),
                category=item.get("category", "OTHER"),
                severity=item.get("severity", "MEDIUM"),
                source_ip=item.get("source_ip"),
                actor=item.get("actor"),
                target=item.get("target"),
                payload=payload,
                masked_payload=masked,
                tags=item.get("tags") or [],
                event_time=event_time,
                digest=digest,
                prev_hash=prev_hash,
                block_index=block_index,
                created_by=created_by or ctx.user_id,
            )
            session.add(row)
            session.flush()
            session.add(models.EvidenceBlock(
                tenant_id=tenant_id, event_id=row.id, block_index=block_index,
                prev_hash=prev_hash, payload_hash=sha256(canonical), root_hash=digest,
                canonical=canonical,
            ))
            events_out.append(row)
            prev_hash = digest
            # Violation detection on ingest (features 426, 442)
            violations = PolicyService.evaluate(session, ctx, row)
            if tenant_id:
                for v in violations:
                    events.publish(session, "siem.policy.violation_detected.v1",
                                   "PolicyViolation", v.id, {
                                       "violation_id": str(v.id),
                                       "policy_id": str(v.policy_id),
                                       "event_id": str(row.id),
                                       "severity": v.severity,
                                       "description": v.description,
                                   }, tenant_id=tenant_id)
            if tenant_id:
                events.publish(session, "siem.security_event.ingested.v1", "SecurityEvent",
                               row.id, {"event_id": str(row.id), "digest": digest,
                                        "event_type": row.event_type,
                                        "severity": row.severity}, tenant_id=tenant_id)
        session.commit()
        return events_out


# ---------------------------------------------------------------------------
# Compliance policies (features 401, 426, 441, 442, 1371)
# ---------------------------------------------------------------------------
class PolicyService:
    @staticmethod
    def create(session: Session, ctx: TenantContext, data: dict) -> models.CompliancePolicy:
        tenant_id = ctx.require_tenant()
        p = models.CompliancePolicy(tenant_id=tenant_id, created_by=ctx.user_id, **data)
        session.add(p)
        session.commit()
        record_audit(session, ctx, "policy.create", "CompliancePolicy", str(p.id))
        session.commit()
        return p

    @staticmethod
    def evaluate(session: Session, ctx: TenantContext, event: models.SecurityEvent,
                 policy_id: uuid.UUID | None = None) -> list[models.PolicyViolation]:
        q = session.query(models.CompliancePolicy).filter(
            models.CompliancePolicy.tenant_id == event.tenant_id,
            models.CompliancePolicy.enabled.is_(True))
        if policy_id:
            q = q.filter(models.CompliancePolicy.id == policy_id)
        found: list[models.PolicyViolation] = []
        for p in q.all():
            if PolicyService._matches(p.rule_json or {}, event):
                v = models.PolicyViolation(
                    tenant_id=event.tenant_id, policy_id=p.id, event_id=event.id,
                    description=f"Policy '{p.name}' matched {event.event_type}",
                    severity=p.severity or event.severity, status="OPEN")
                session.add(v)
                found.append(v)
        if found:
            session.flush()
        return found

    @staticmethod
    def _matches(rule: dict, event: models.SecurityEvent) -> bool:
        field = rule.get("field")
        op = rule.get("op", "eq")
        value = rule.get("value")
        if not field:
            return False
        actual = getattr(event, field, None)
        if actual is None and field in ("severity", "category", "event_type"):
            return False
        if isinstance(actual, str):
            if op == "eq":
                return actual.lower() == str(value).lower()
            if op == "in":
                return actual.lower() in {str(x).lower() for x in (value or [])}
            if op == "ne":
                return actual.lower() != str(value).lower()
        if isinstance(actual, (int, float)):
            if op in ("gte", ">="):
                return actual >= float(value)
            if op in ("lte", "<="):
                return actual <= float(value)
            if op == "eq":
                return actual == float(value)
        return False

    @staticmethod
    def resolve(session: Session, ctx: TenantContext, violation_id: uuid.UUID) -> models.PolicyViolation:
        v = session.query(models.PolicyViolation).filter(
            models.PolicyViolation.id == violation_id,
            models.PolicyViolation.tenant_id == ctx.require_tenant()).first()
        if not v:
            raise KeyError("Violation not found")
        v.status = "RESOLVED"
        v.resolved_at = _utcnow()
        session.commit()
        record_audit(session, ctx, "violation.resolve", "PolicyViolation", str(v.id))
        session.commit()
        return v


# ---------------------------------------------------------------------------
# Retention (features 404, 405, 406, 1334)
# ---------------------------------------------------------------------------
class RetentionService:
    @staticmethod
    def set_policy(session: Session, ctx: TenantContext, data: dict) -> models.RetentionPolicy:
        tenant_id = ctx.require_tenant()
        existing = session.query(models.RetentionPolicy).filter(
            models.RetentionPolicy.tenant_id == tenant_id,
            models.RetentionPolicy.data_class == data["data_class"]).first()
        if existing:
            for k, v in data.items():
                setattr(existing, k, v)
            row = existing
        else:
            row = models.RetentionPolicy(tenant_id=tenant_id, **data)
            session.add(row)
        session.commit()
        record_audit(session, ctx, "retention.set", "RetentionPolicy", str(row.id))
        session.commit()
        return row

    @staticmethod
    def apply(session: Session, ctx: TenantContext, data_class: str | None = None) -> dict:
        tenant_id = ctx.require_tenant()
        q = session.query(models.RetentionPolicy).filter(
            models.RetentionPolicy.tenant_id == tenant_id,
            models.RetentionPolicy.enabled.is_(True))
        if data_class:
            q = q.filter(models.RetentionPolicy.data_class == data_class)
        summary = {"archived": 0, "purged": 0, "checked": 0}
        cutoff = _utcnow() - timedelta(days=30)  # baseline guard
        for p in q.all():
            threshold = _utcnow() - timedelta(days=p.retention_days)
            if p.data_class == DataClass.SECURITY_EVENT.value:
                rows = session.query(models.SecurityEvent).filter(
                    models.SecurityEvent.tenant_id == tenant_id,
                    models.SecurityEvent.received_at < threshold).all()
                for r in rows:
                    if p.action == RetentionAction.ARCHIVE.value:
                        r.archived = True
                        summary["archived"] += 1
                    else:
                        session.delete(r)
                        summary["purged"] += 1
            elif p.data_class == DataClass.AUDIT_LOG.value:
                rows = session.query(models.AuditLog).filter(
                    models.AuditLog.tenant_id == tenant_id,
                    models.AuditLog.created_at < threshold).all()
                for r in rows:
                    session.delete(r)
                    summary["purged"] += 1
            elif p.data_class == DataClass.CONSENT.value:
                rows = session.query(models.ConsentRecord).filter(
                    models.ConsentRecord.tenant_id == tenant_id,
                    models.ConsentRecord.granted_at < threshold).all()
                for r in rows:
                    r.status = "REVOKED"
                    r.revoked_at = _utcnow()
                    summary["archived"] += 1
            summary["checked"] += 1
        session.commit()
        if summary["purged"] or summary["archived"]:
            events.publish(session, "siem.retention.purged.v1", "RetentionPolicy",
                           str(tenant_id), summary, tenant_id=tenant_id)
            session.commit()
        return summary


# ---------------------------------------------------------------------------
# Consent + DSAR (features 421, 422, 423, 1240)
# ---------------------------------------------------------------------------
class ConsentService:
    @staticmethod
    def set(session: Session, ctx: TenantContext, data: dict) -> models.ConsentRecord:
        tenant_id = ctx.require_tenant()
        row = session.query(models.ConsentRecord).filter(
            models.ConsentRecord.tenant_id == tenant_id,
            models.ConsentRecord.subscriber_id == data["subscriber_id"],
            models.ConsentRecord.purpose == data["purpose"]).first()
        if row:
            row.status = data.get("status", "GRANTED")
            row.source = data.get("source")
            row.revoked_at = _utcnow() if row.status == "REVOKED" else None
        else:
            row = models.ConsentRecord(tenant_id=tenant_id, **data)
            session.add(row)
        session.commit()
        events.publish(session, "siem.consent.updated.v1", "ConsentRecord", row.id,
                       {"subscriber_id": row.subscriber_id, "purpose": row.purpose,
                        "status": row.status}, tenant_id=tenant_id)
        session.commit()
        record_audit(session, ctx, "consent.set", "ConsentRecord", str(row.id))
        session.commit()
        return row


class DsarService:
    @staticmethod
    def create(session: Session, ctx: TenantContext, data: dict) -> models.DataAccessRequest:
        tenant_id = ctx.require_tenant()
        r = models.DataAccessRequest(tenant_id=tenant_id, **data)
        session.add(r)
        session.commit()
        record_audit(session, ctx, "dsar.create", "DataAccessRequest", str(r.id))
        session.commit()
        return r

    @staticmethod
    def fulfill(session: Session, ctx: TenantContext, req_id: uuid.UUID) -> models.DataAccessRequest:
        r = session.query(models.DataAccessRequest).filter(
            models.DataAccessRequest.id == req_id,
            models.DataAccessRequest.tenant_id == ctx.require_tenant()).first()
        if not r:
            raise KeyError("Data request not found")
        r.status = "FULFILLED"
        r.fulfilled_at = _utcnow()
        session.commit()
        events.publish(session, "siem.data_request.completed.v1", "DataAccessRequest", r.id,
                       {"request_id": str(r.id), "type": r.request_type,
                        "subject_id": r.subject_id}, tenant_id=r.tenant_id)
        session.commit()
        record_audit(session, ctx, "dsar.fulfill", "DataAccessRequest", str(r.id))
        session.commit()
        return r

    @staticmethod
    def erase(session: Session, ctx: TenantContext, req_id: uuid.UUID) -> models.DataAccessRequest:
        """Right to erasure (423): purge PII-bearing records for the subject."""
        r = session.query(models.DataAccessRequest).filter(
            models.DataAccessRequest.id == req_id,
            models.DataAccessRequest.tenant_id == ctx.require_tenant(),
            models.DataAccessRequest.request_type == "ERASURE").first()
        if not r:
            raise KeyError("Erasure request not found")
        tenant_id = r.tenant_id
        session.query(models.ConsentRecord).filter(
            models.ConsentRecord.tenant_id == tenant_id,
            models.ConsentRecord.subscriber_id == r.subject_id).delete()
        session.query(models.SecurityEvent).filter(
            models.SecurityEvent.tenant_id == tenant_id,
            models.SecurityEvent.actor == r.subject_id).delete()
        r.status = "FULFILLED"
        r.fulfilled_at = _utcnow()
        session.commit()
        events.publish(session, "siem.data_request.completed.v1", "DataAccessRequest", r.id,
                       {"request_id": str(r.id), "type": "ERASURE",
                        "subject_id": r.subject_id}, tenant_id=tenant_id)
        session.commit()
        record_audit(session, ctx, "dsar.erase", "DataAccessRequest", str(r.id))
        session.commit()
        return r


# ---------------------------------------------------------------------------
# Security cases + SOC workflow (features 1414, 1415, 1471, 1472, 1473, 1474)
# ---------------------------------------------------------------------------
class CaseService:
    @staticmethod
    def create(session: Session, ctx: TenantContext, data: dict) -> models.SecurityCase:
        tenant_id = ctx.require_tenant()
        ref = f"CASE-{tenant_id.hex[:4].upper()}-{uuid.uuid4().hex[:8].upper()}"
        severity = data.get("severity", "MEDIUM")
        case = models.SecurityCase(tenant_id=tenant_id, ref_id=ref, **data)
        session.add(case)
        session.flush()
        session.add(models.CaseEvent(case_id=case.id, tenant_id=tenant_id,
                                    from_state=None, to_state="OPEN",
                                    transition=None, note="Case opened",
                                    actor=ctx.user_id))
        case.priority_score = SEVERITY_WEIGHT.get(severity, 20)
        session.commit()
        events.publish(session, "siem.case.created.v1", "SecurityCase", case.id,
                       {"case_id": str(case.id), "ref_id": ref, "severity": severity,
                        "status": "OPEN"}, tenant_id=tenant_id)
        session.commit()
        record_audit(session, ctx, "case.create", "SecurityCase", str(case.id))
        session.commit()
        return case

    @staticmethod
    def transition(session: Session, ctx: TenantContext, case_id: uuid.UUID,
                   transition: str, note: str | None = None) -> models.SecurityCase:
        case = session.query(models.SecurityCase).filter(
            models.SecurityCase.id == case_id,
            models.SecurityCase.tenant_id == ctx.require_tenant()).first()
        if not case:
            raise KeyError("Case not found")
        current = CaseStatus(case.status)
        tr = CaseTransition(transition)
        allowed = CASE_FLOW.get(current, {})
        if tr not in allowed:
            raise ValueError(f"Invalid transition {transition} from {case.status}")
        to_state = allowed[tr].value
        from_state = case.status
        case.status = to_state
        if to_state == CaseStatus.CLOSED.value:
            case.closed_at = _utcnow()
        session.add(models.CaseEvent(case_id=case.id, tenant_id=case.tenant_id,
                                    from_state=from_state, to_state=to_state,
                                    transition=transition, note=note, actor=ctx.user_id))
        session.commit()
        events.publish(session, "siem.case.transitioned.v1", "SecurityCase", case.id,
                       {"case_id": str(case.id), "from": from_state, "to": to_state,
                        "transition": transition}, tenant_id=case.tenant_id)
        session.commit()
        record_audit(session, ctx, "case.transition", "SecurityCase", str(case.id),
                     detail={"from": from_state, "to": to_state})
        session.commit()
        return case

    @staticmethod
    def escalate(session: Session, ctx: TenantContext, case_id: uuid.UUID) -> models.SecurityCase:
        case = session.query(models.SecurityCase).filter(
            models.SecurityCase.id == case_id,
            models.SecurityCase.tenant_id == ctx.require_tenant()).first()
        if not case:
            raise KeyError("Case not found")
        if case.severity in ("HIGH", "CRITICAL"):
            case.escalated = True
            case.priority_score = max(case.priority_score, 90.0)
        session.add(models.CaseEvent(case_id=case.id, tenant_id=case.tenant_id,
                                    from_state=case.status, to_state=case.status,
                                    transition="ESCALATE",
                                    note="Escalated per severity matrix",
                                    actor=ctx.user_id))
        session.commit()
        if case.escalated:
            events.publish(session, "siem.case.escalated.v1", "SecurityCase", case.id,
                           {"case_id": str(case.id), "severity": case.severity,
                            "priority": case.priority_score}, tenant_id=case.tenant_id)
            session.commit()
        record_audit(session, ctx, "case.escalate", "SecurityCase", str(case.id))
        session.commit()
        return case

    @staticmethod
    def assess_impact(session: Session, ctx: TenantContext, case_id: uuid.UUID) -> models.SecurityCase:
        case = session.query(models.SecurityCase).filter(
            models.SecurityCase.id == case_id,
            models.SecurityCase.tenant_id == ctx.require_tenant()).first()
        if not case:
            raise KeyError("Case not found")
        severity = SEVERITY_WEIGHT.get(case.severity, 20)
        exposure = len(case.linked_event_ids or [])
        case.impact_score = round(severity * 0.6 + min(exposure, 20) * 2.0, 2)
        case.breach_impact = {
            "severity_weight": severity,
            "event_count": exposure,
            "estimated_affected": max(1, exposure * 3),
            "risk_level": "HIGH" if case.impact_score >= 60 else "MEDIUM" if case.impact_score >= 30 else "LOW",
        }
        session.commit()
        record_audit(session, ctx, "case.impact_assess", "SecurityCase", str(case.id))
        session.commit()
        return case

    @staticmethod
    def notify(session: Session, ctx: TenantContext, case_id: uuid.UUID, payload: dict) -> dict:
        case = session.query(models.SecurityCase).filter(
            models.SecurityCase.id == case_id,
            models.SecurityCase.tenant_id == ctx.require_tenant()).first()
        if not case:
            raise KeyError("Case not found")
        case.notification_tracked = True
        notification = {"case_id": str(case.id), "ref_id": case.ref_id,
                        "channel": payload.get("channel", "EMAIL"),
                        "audience": payload.get("audience", "REGULATOR"),
                        "message": payload.get("message") or f"Breach notification for {case.title}",
                        "notified_at": _utcnow().isoformat()}
        session.commit()
        events.publish(session, "siem.breach.notified.v1", "SecurityCase", case.id,
                       notification, tenant_id=case.tenant_id)
        session.commit()
        record_audit(session, ctx, "breach.notify", "SecurityCase", str(case.id))
        session.commit()
        return notification


# ---------------------------------------------------------------------------
# LI enablement (features 411-416)
# ---------------------------------------------------------------------------
class LiService:
    @staticmethod
    def request(session: Session, ctx: TenantContext, data: dict) -> models.LIRequest:
        tenant_id = ctx.require_tenant()
        r = models.LIRequest(tenant_id=tenant_id, **data)
        session.add(r)
        session.commit()
        record_audit(session, ctx, "li.request", "LIRequest", str(r.id))
        session.commit()
        return r

    @staticmethod
    def decide(session: Session, ctx: TenantContext, req_id: uuid.UUID, decision: str,
               note: str | None = None) -> models.LIRequest:
        r = session.query(models.LIRequest).filter(
            models.LIRequest.id == req_id,
            models.LIRequest.tenant_id == ctx.require_tenant()).first()
        if not r:
            raise KeyError("LI request not found")
        if decision not in ("APPROVED", "REJECTED"):
            raise ValueError("decision must be APPROVED or REJECTED")
        r.status = decision
        r.approved_by = ctx.user_id
        r.approver_note = note
        r.decided_at = _utcnow()
        session.commit()
        record_audit(session, ctx, "li.decide", "LIRequest", str(r.id),
                     detail={"decision": decision})
        session.commit()
        return r


# ---------------------------------------------------------------------------
# Vulnerabilities (features 1173, 1174, 1175)
# ---------------------------------------------------------------------------
class VulnerabilityService:
    @staticmethod
    def ingest(session: Session, ctx: TenantContext, data: dict) -> models.Vulnerability:
        tenant_id = ctx.require_tenant()
        v = models.Vulnerability(tenant_id=tenant_id, **data)
        session.add(v)
        session.commit()
        events.publish(session, "siem.vulnerability.ingested.v1", "Vulnerability", v.id,
                       {"vuln_id": str(v.id), "target": v.target, "severity": v.severity,
                        "cve": v.cve}, tenant_id=tenant_id)
        session.commit()
        record_audit(session, ctx, "vuln.ingest", "Vulnerability", str(v.id))
        session.commit()
        return v

    @staticmethod
    def remediate(session: Session, ctx: TenantContext, vuln_id: uuid.UUID) -> models.Vulnerability:
        v = session.query(models.Vulnerability).filter(
            models.Vulnerability.id == vuln_id,
            models.Vulnerability.tenant_id == ctx.require_tenant()).first()
        if not v:
            raise KeyError("Vulnerability not found")
        v.status = "REMEDIATED"
        v.remediated_at = _utcnow()
        session.commit()
        record_audit(session, ctx, "vuln.remediate", "Vulnerability", str(v.id))
        session.commit()
        return v
