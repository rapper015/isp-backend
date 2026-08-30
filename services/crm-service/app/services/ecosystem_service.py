"""CRM partner, ecosystem, SLA/automation services (Master Spec Batch 6)."""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import (FederationLink, Partner, PartnerHierarchyNode,
                      PartnerPerformanceRecord, ResellerRegulatoryRecord,
                      TicketEscalation, TicketSlaTimer, TicketSuggestion)
from ..services.audit_service import audit, correlation, outbox


def _now():
    return datetime.now(timezone.utc)


def _no_tenant(data: dict) -> dict:
    return {k: v for k, v in data.items() if k != "tenant_id"}


class PartnerService:
    @staticmethod
    def create(session: Session, tenant_id, data: dict, actor: str = "system") -> Partner:
        p = Partner(tenant_id=tenant_id, status="ACTIVE", **_no_tenant(data))
        session.add(p)
        session.flush()
        outbox(session, "crm.partner.created.v1", tenant_id, correlation(None),
               {"partner_id": str(p.id), "code": p.code, "name": p.name})
        audit(session, tenant_id, actor, "partner.create", "Partner", p.id,
              safe_after={"code": p.code, "name": p.name})
        session.commit()
        return p

    @staticmethod
    def record_performance(session: Session, tenant_id, partner_id: uuid.UUID, period: str,
                           kpi: dict, actor: str = "system") -> PartnerPerformanceRecord:
        row = session.query(PartnerPerformanceRecord).filter(
            PartnerPerformanceRecord.tenant_id == tenant_id,
            PartnerPerformanceRecord.partner_id == partner_id,
            PartnerPerformanceRecord.period == period).first()
        if row:
            row.kpi = kpi
        else:
            row = PartnerPerformanceRecord(tenant_id=tenant_id, partner_id=partner_id,
                                           period=period, kpi=kpi)
            session.add(row)
        session.flush()
        outbox(session, "crm.partner.performance.updated.v1", tenant_id, correlation(None),
               {"partner_id": str(partner_id), "period": period, "kpi": kpi})
        session.commit()
        return row

    @staticmethod
    def evaluate_sla(session: Session, tenant_id, partner_id: uuid.UUID,
                     actor: str = "system") -> Partner:
        """Partner SLA management (825): recompute SLA compliance + score."""
        p = session.query(Partner).filter(
            Partner.id == partner_id, Partner.tenant_id == tenant_id).first()
        if not p:
            raise KeyError("partner not found")
        perf = session.query(PartnerPerformanceRecord).filter(
            PartnerPerformanceRecord.partner_id == partner_id,
            PartnerPerformanceRecord.tenant_id == tenant_id).order_by(
            PartnerPerformanceRecord.period.desc()).first()
        kpi = perf.kpi if perf else {}
        total = float(kpi.get("orders", 0))
        late = float(kpi.get("late_orders", 0))
        p.breaches = int(late)
        p.sla_pct = round(100 * (1 - late / total), 2) if total else 100.0
        p.performance_score = round(p.sla_pct * 0.7 + 30 * min(float(kpi.get("conversions", 0)) / (total or 1), 1), 2)
        session.flush()
        outbox(session, "crm.partner.sla_evaluated.v1", tenant_id, correlation(None),
               {"partner_id": str(partner_id), "sla_pct": p.sla_pct,
                "performance_score": p.performance_score})
        session.commit()
        return p

    @staticmethod
    def add_hierarchy(session: Session, tenant_id, partner_id: uuid.UUID,
                      parent_id: uuid.UUID | None) -> PartnerHierarchyNode:
        level = 1
        if parent_id:
            parent = session.query(PartnerHierarchyNode).filter(
                PartnerHierarchyNode.tenant_id == tenant_id,
                PartnerHierarchyNode.partner_id == parent_id).first()
            if not parent:
                # Parent partner may exist without a node yet; auto-create as root.
                if not session.query(Partner).filter(
                        Partner.id == parent_id, Partner.tenant_id == tenant_id).first():
                    raise KeyError("parent partner not in hierarchy")
                parent = PartnerHierarchyNode(tenant_id=tenant_id, partner_id=parent_id,
                                              parent_id=None, level=1)
                session.add(parent)
                session.flush()
            level = parent.level + 1
        node = session.query(PartnerHierarchyNode).filter(
            PartnerHierarchyNode.tenant_id == tenant_id,
            PartnerHierarchyNode.partner_id == partner_id).first()
        if node:
            node.parent_id, node.level = parent_id, level
        else:
            node = PartnerHierarchyNode(tenant_id=tenant_id, partner_id=partner_id,
                                        parent_id=parent_id, level=level)
            session.add(node)
        session.commit()
        return node

    @staticmethod
    def tree(session: Session, tenant_id) -> list[dict]:
        nodes = session.query(PartnerHierarchyNode).filter(
            PartnerHierarchyNode.tenant_id == tenant_id).all()
        names = {str(p.id): p.name for p in session.query(Partner).filter(
            Partner.tenant_id == tenant_id).all()}
        by_id = {str(n.partner_id): {"partner_id": str(n.partner_id), "name": names.get(str(n.partner_id)),
                                     "level": n.level, "children": []} for n in nodes}
        roots = []
        for n in nodes:
            node = by_id[str(n.partner_id)]
            if n.parent_id and str(n.parent_id) in by_id:
                by_id[str(n.parent_id)]["children"].append(node)
            else:
                roots.append(node)
        return roots


class FederationService:
    @staticmethod
    def create_link(session: Session, tenant_id, data: dict, actor: str = "system") -> FederationLink:
        link = FederationLink(tenant_id=tenant_id, status="LINKED", **_no_tenant(data))
        session.add(link)
        session.flush()
        outbox(session, "crm.federation.linked.v1", tenant_id, correlation(None),
               {"operator_name": link.operator_name, "direction": link.direction,
                "protocol": link.protocol})
        audit(session, tenant_id, actor, "federation.link", "FederationLink", link.id,
              safe_after={"operator_name": link.operator_name})
        session.commit()
        return link


class TicketSlaService:
    @staticmethod
    def start_timer(session: Session, tenant_id, ticket_id: str, sla_minutes: int,
                    actor: str = "system") -> TicketSlaTimer:
        t = session.query(TicketSlaTimer).filter(
            TicketSlaTimer.tenant_id == tenant_id,
            TicketSlaTimer.ticket_id == ticket_id).first()
        deadline = _now() + timedelta(minutes=sla_minutes)
        if t:
            t.sla_minutes, t.deadline, t.breached = sla_minutes, deadline, False
        else:
            t = TicketSlaTimer(tenant_id=tenant_id, ticket_id=ticket_id,
                               sla_minutes=sla_minutes, deadline=deadline)
            session.add(t)
        session.commit()
        return t

    @staticmethod
    def evaluate(session: Session, tenant_id) -> list[dict]:
        """SLA Timer (310): mark breaches for open timers past deadline."""
        breached = []
        rows = session.query(TicketSlaTimer).filter(
            TicketSlaTimer.tenant_id == tenant_id,
            TicketSlaTimer.deadline < _now(),
            TicketSlaTimer.breached.is_(False),
            TicketSlaTimer.resolved_at.is_(None)).all()
        for t in rows:
            t.breached = True
            breached.append({"ticket_id": t.ticket_id, "deadline": t.deadline})
            outbox(session, "crm.ticket.sla_breached.v1", tenant_id, correlation(None),
                   {"ticket_id": t.ticket_id, "deadline": t.deadline.isoformat()})
        session.commit()
        return breached

    @staticmethod
    def resolve(session: Session, tenant_id, ticket_id: str) -> TicketSlaTimer:
        t = session.query(TicketSlaTimer).filter(
            TicketSlaTimer.tenant_id == tenant_id,
            TicketSlaTimer.ticket_id == ticket_id).first()
        if not t:
            raise KeyError("sla timer not found")
        t.resolved_at = _now()
        t.breached = t.resolved_at.replace(tzinfo=None) > t.deadline
        session.commit()
        return t


class EscalationService:
    @staticmethod
    def escalate(session: Session, tenant_id, ticket_id: str, level: str,
                 reason: str | None, actor: str = "system") -> TicketEscalation:
        e = TicketEscalation(tenant_id=tenant_id, ticket_id=ticket_id, level=level,
                             reason=reason, status="OPEN")
        session.add(e)
        session.flush()
        outbox(session, "crm.ticket.escalated.v1", tenant_id, correlation(None),
               {"ticket_id": ticket_id, "level": level, "reason": reason})
        audit(session, tenant_id, actor, "ticket.escalate", "TicketEscalation", e.id,
              safe_after={"ticket_id": ticket_id, "level": level})
        session.commit()
        return e

    @staticmethod
    def resolve(session: Session, tenant_id, escalation_id: uuid.UUID) -> TicketEscalation:
        e = session.query(TicketEscalation).filter(
            TicketEscalation.id == escalation_id,
            TicketEscalation.tenant_id == tenant_id).first()
        if not e:
            raise KeyError("escalation not found")
        e.status = "RESOLVED"
        e.resolved_at = _now()
        session.commit()
        return e


class SuggestionService:
    _RULES = (
        ("signal", "Check ONT optical power; re-terminate connector if low."),
        ("wifi", "Reboot the router and verify 2.4/5 GHz SSID broadcast."),
        ("slow", "Run a speed test on wired; check CGNAT/port forwarding and plan FUP."),
        ("billing", "Verify payment captured and reconcile invoice balance."),
        ("router", "Factory reset the CPE and reprovision via the ACS."),
        ("password", "Reset the subscriber PPPoE/portal password via the identity store."),
    )

    @staticmethod
    def suggest(session: Session, tenant_id, ticket_id: str, issue: str,
                actor: str = "system") -> TicketSuggestion:
        """Suggested Resolutions (1191): keyword-matched resolution playbooks."""
        lowered = (issue or "").lower()
        matched = next((text for kw, text in SuggestionService._RULES if kw in lowered), None)
        if not matched:
            matched = "Escalate to the NOC with the latest diagnostics snapshot."
        suggestion = TicketSuggestion(tenant_id=tenant_id, ticket_id=ticket_id,
                                      suggestion=matched, source="AI", confidence=0.7)
        session.add(suggestion)
        session.flush()
        outbox(session, "crm.suggestion.generated.v1", tenant_id, correlation(None),
               {"ticket_id": ticket_id, "suggestion": matched})
        session.commit()
        return suggestion


class RegulatoryService:
    @staticmethod
    def track(session: Session, tenant_id, reseller_id: str, report_type: str,
              actor: str = "system") -> ResellerRegulatoryRecord:
        """Regulatory Tracking (391): reseller compliance reporting log."""
        r = session.query(ResellerRegulatoryRecord).filter(
            ResellerRegulatoryRecord.tenant_id == tenant_id,
            ResellerRegulatoryRecord.reseller_id == reseller_id,
            ResellerRegulatoryRecord.report_type == report_type).first()
        if r:
            r.status = "TRACKED"
            r.submitted_at = None
        else:
            r = ResellerRegulatoryRecord(tenant_id=tenant_id, reseller_id=reseller_id,
                                         report_type=report_type, status="TRACKED")
            session.add(r)
        session.flush()
        outbox(session, "crm.regulatory.tracked.v1", tenant_id, correlation(None),
               {"reseller_id": reseller_id, "report_type": report_type})
        session.commit()
        return r

    @staticmethod
    def submit(session: Session, tenant_id, reseller_id: str, report_type: str) -> ResellerRegulatoryRecord:
        r = session.query(ResellerRegulatoryRecord).filter(
            ResellerRegulatoryRecord.tenant_id == tenant_id,
            ResellerRegulatoryRecord.reseller_id == reseller_id,
            ResellerRegulatoryRecord.report_type == report_type).first()
        if not r:
            raise KeyError("regulatory record not found")
        r.status = "SUBMITTED"
        r.submitted_at = _now()
        session.commit()
        return r
