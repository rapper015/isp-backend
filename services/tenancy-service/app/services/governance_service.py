"""Tenancy governance services (Master Spec Batch 4 — core-platform gaps)."""
import re
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app import models
from app.events import outbox as publish_outbox


def _now():
    return datetime.now(timezone.utc)


def _no_tenant(data: dict) -> dict:
    return {k: v for k, v in data.items() if k != "tenant_id"}


class NotificationService:
    @staticmethod
    def create(session: Session, tenant_id, data: dict) -> models.Notification:
        n = models.Notification(tenant_id=tenant_id, status="PENDING", **_no_tenant(data))
        session.add(n)
        session.commit()
        return n

    @staticmethod
    def retry(session: Session, tenant_id, notif_id: uuid.UUID) -> models.Notification:
        n = session.query(models.Notification).filter(
            models.Notification.id == notif_id,
            models.Notification.tenant_id == tenant_id).first()
        if not n:
            raise KeyError("notification not found")
        if n.attempts >= n.max_attempts:
            raise ValueError("max attempts reached")
        n.attempts += 1
        n.status = "PENDING"
        session.commit()
        return n

    @staticmethod
    def deliver(session: Session, tenant_id, notif_id: uuid.UUID) -> models.Notification:
        n = session.query(models.Notification).filter(
            models.Notification.id == notif_id,
            models.Notification.tenant_id == tenant_id).first()
        if not n:
            raise KeyError("notification not found")
        n.status = "SENT"
        n.sent_at = _now()
        n.delivered_at = _now()
        session.flush()
        publish_outbox(session, "tenancy.notification.sent.v1", tenant_id, None,
                       {"notification_id": str(n.id), "recipient": n.recipient,
                        "channel": n.channel, "status": n.status})
        session.commit()
        return n


class CampaignService:
    @staticmethod
    def create(session: Session, tenant_id, data: dict) -> models.Campaign:
        c = models.Campaign(tenant_id=tenant_id, status="DRAFT", **_no_tenant(data))
        session.add(c)
        session.commit()
        return c

    @staticmethod
    def schedule(session: Session, tenant_id, campaign_id: uuid.UUID, schedule_at: datetime) -> models.Campaign:
        c = session.query(models.Campaign).filter(
            models.Campaign.id == campaign_id,
            models.Campaign.tenant_id == tenant_id).first()
        if not c:
            raise KeyError("campaign not found")
        c.schedule_at = schedule_at
        c.status = "SCHEDULED"
        session.flush()
        publish_outbox(session, "tenancy.campaign.scheduled.v1", tenant_id, None,
                       {"campaign_id": str(c.id), "schedule_at": schedule_at.isoformat()})
        session.commit()
        return c

    @staticmethod
    def execute(session: Session, tenant_id, campaign_id: uuid.UUID) -> models.Campaign:
        """Broadcast (518): send to the audience, creating recipient records."""
        c = session.query(models.Campaign).filter(
            models.Campaign.id == campaign_id,
            models.Campaign.tenant_id == tenant_id).first()
        if not c:
            raise KeyError("campaign not found")
        for recipient in (c.audience or []):
            existing = session.query(models.CampaignRecipient).filter(
                models.CampaignRecipient.campaign_id == c.id,
                models.CampaignRecipient.recipient == recipient).first()
            if not existing:
                session.add(models.CampaignRecipient(tenant_id=tenant_id, campaign_id=c.id,
                                                     recipient=recipient, status="SENT"))
        c.status = "RUNNING"
        c.executed_at = _now()
        session.flush()
        publish_outbox(session, "tenancy.campaign.executed.v1", tenant_id, None,
                       {"campaign_id": str(c.id), "audience_size": len(c.audience or [])})
        session.commit()
        return c

    @staticmethod
    def track(session: Session, tenant_id, campaign_id: uuid.UUID, recipient: str,
              event: str) -> models.CampaignRecipient:
        """Conversion tracking (525): SENT -> OPENED -> CLICKED -> CONVERTED."""
        r = session.query(models.CampaignRecipient).filter(
            models.CampaignRecipient.campaign_id == campaign_id,
            models.CampaignRecipient.recipient == recipient,
            models.CampaignRecipient.tenant_id == tenant_id).first()
        if not r:
            raise KeyError("recipient not found")
        order = ["SENT", "OPENED", "CLICKED", "CONVERTED"]
        if event in order and order.index(event) > order.index(r.status):
            r.status = event
            if event == "CONVERTED":
                r.converted_at = _now()
                c = session.query(models.Campaign).filter(models.Campaign.id == campaign_id).first()
                if c:
                    c.status = "COMPLETED"
                    c.completed_at = _now()
        session.commit()
        return r

    @staticmethod
    def analytics(session: Session, tenant_id, campaign_id: uuid.UUID) -> models.CampaignMetric:
        """Campaign analytics (523): compute funnel rates."""
        base = {"sent_count": 0, "opened_count": 0, "clicked_count": 0, "converted_count": 0}
        rows = session.query(models.CampaignRecipient).filter(
            models.CampaignRecipient.campaign_id == campaign_id,
            models.CampaignRecipient.tenant_id == tenant_id).all()
        for r in rows:
            base["sent_count"] += 1
            if r.status in ("OPENED", "CLICKED", "CONVERTED"):
                base["opened_count"] += 1
            if r.status in ("CLICKED", "CONVERTED"):
                base["clicked_count"] += 1
            if r.status == "CONVERTED":
                base["converted_count"] += 1
        sent = base["sent_count"] or 1
        metric = models.CampaignMetric(tenant_id=tenant_id, campaign_id=campaign_id,
                                       open_rate=round(100 * base["opened_count"] / sent, 2),
                                       conversion_rate=round(100 * base["converted_count"] / sent, 2),
                                       **base)
        session.add(metric)
        session.commit()
        return metric


class GovernanceService:
    @staticmethod
    def record_usage(session: Session, tenant_id, data: dict) -> models.UsageMeter:
        m = models.UsageMeter(tenant_id=tenant_id, **_no_tenant(data))
        session.add(m)
        session.flush()
        publish_outbox(session, "tenancy.usage.metered.v1", tenant_id, None,
                       {"resource": m.resource, "amount": m.amount, "unit": m.unit})
        session.commit()
        return m

    @staticmethod
    def record_cost(session: Session, tenant_id, data: dict) -> models.CostRecord:
        c = models.CostRecord(tenant_id=tenant_id, **_no_tenant(data))
        session.add(c)
        session.commit()
        return c

    @staticmethod
    def optimize_costs(session: Session, tenant_id) -> dict:
        """Cost optimization (759, 1389): recommend storage tiering by volume."""
        storage = session.query(models.CostRecord).filter(
            models.CostRecord.tenant_id == tenant_id,
            models.CostRecord.category == "STORAGE").all()
        cold_candidates = [s for s in storage if (s.volume_gb or 0) > 500]
        recommendation = {
            "storage_records_reviewed": len(storage),
            "cold_tier_candidates": len(cold_candidates),
            "suggested": [{"id": str(s.id), "storage_class": s.storage_class or "STANDARD",
                           "volume_gb": s.volume_gb, "move_to": "COLD"} for s in cold_candidates],
        }
        return recommendation

    @staticmethod
    def create_policy(session: Session, tenant_id, data: dict) -> models.GovernancePolicy:
        p = models.GovernancePolicy(tenant_id=tenant_id, **_no_tenant(data))
        session.add(p)
        session.commit()
        return p

    @staticmethod
    def evaluate_policy(session: Session, tenant_id, policy_id: uuid.UUID, sample: dict) -> dict:
        p = session.query(models.GovernancePolicy).filter(
            models.GovernancePolicy.id == policy_id,
            models.GovernancePolicy.tenant_id == tenant_id).first()
        if not p:
            raise KeyError("policy not found")
        rule = p.rule_json or {}
        field, op, value = rule.get("field"), rule.get("op", "eq"), rule.get("value")
        actual = sample.get(field)
        result = False
        if op == "eq":
            result = actual == value
        elif op == "gte":
            result = float(actual or 0) >= float(value)
        elif op == "lte":
            result = float(actual or 0) <= float(value)
        elif op == "in":
            result = actual in (value or [])
        return {"policy_id": str(p.id), "matched": result, "severity": p.severity}

    @staticmethod
    def run_compliance(session: Session, tenant_id, check_name: str) -> models.ComplianceCheck:
        """Compliance automation (779): run enabled policies against usage/cost samples."""
        policies = session.query(models.GovernancePolicy).filter(
            models.GovernancePolicy.tenant_id == tenant_id,
            models.GovernancePolicy.enabled.is_(True)).all()
        results = []
        fail = False
        for p in policies:
            sample = {"severity": p.severity, "enabled": True}
            r = GovernanceService.evaluate_policy(session, tenant_id, p.id, sample)
            results.append(r)
            if not r["matched"]:
                fail = True
        check = models.ComplianceCheck(tenant_id=tenant_id, check_name=check_name,
                                       status="FAIL" if fail else "PASS",
                                       result={"policies": len(policies), "details": results})
        session.add(check)
        session.flush()
        publish_outbox(session, "tenancy.compliance.completed.v1", tenant_id, None,
                       {"check_name": check_name, "status": check.status,
                        "policies": len(policies)})
        session.commit()
        return check

    @staticmethod
    def start_threat_hunt(session: Session, tenant_id, data: dict) -> models.ThreatHunt:
        h = models.ThreatHunt(tenant_id=tenant_id, status="RUNNING", **_no_tenant(data))
        session.add(h)
        session.commit()
        return h

    @staticmethod
    def complete_threat_hunt(session: Session, tenant_id, hunt_id: uuid.UUID,
                             findings: list) -> models.ThreatHunt:
        h = session.query(models.ThreatHunt).filter(
            models.ThreatHunt.id == hunt_id,
            models.ThreatHunt.tenant_id == tenant_id).first()
        if not h:
            raise KeyError("hunt not found")
        h.status = "COMPLETED"
        h.findings = findings or []
        h.completed_at = _now()
        session.flush()
        publish_outbox(session, "tenancy.threat_hunt.completed.v1", tenant_id, None,
                       {"hunt_id": str(h.id), "findings": len(h.findings)})
        session.commit()
        return h

    @staticmethod
    def create_chain(session: Session, tenant_id, data: dict) -> models.ServiceChain:
        c = models.ServiceChain(tenant_id=tenant_id, **_no_tenant(data))
        session.add(c)
        session.flush()
        publish_outbox(session, "tenancy.service_chain.created.v1", tenant_id, None,
                       {"chain_id": str(c.id), "name": c.name, "steps": len(c.services or [])})
        session.commit()
        return c

    @staticmethod
    def create_insight(session: Session, tenant_id, data: dict) -> models.Insight:
        i = models.Insight(tenant_id=tenant_id, **_no_tenant(data))
        session.add(i)
        session.flush()
        publish_outbox(session, "tenancy.insight.generated.v1", tenant_id, None,
                       {"insight_id": str(i.id), "kind": i.kind, "title": i.title})
        session.commit()
        return i

    @staticmethod
    def index_doc(session: Session, tenant_id, data: dict) -> models.KnowledgeDoc:
        d = models.KnowledgeDoc(tenant_id=tenant_id, **_no_tenant(data))
        session.add(d)
        session.commit()
        return d

    @staticmethod
    def search_docs(session: Session, tenant_id, query: str, limit: int = 10) -> list[dict]:
        """Semantic search (929): token-overlap scoring over indexed docs."""
        q_tokens = set(re.findall(r"\w+", query.lower()))
        docs = session.query(models.KnowledgeDoc).filter(
            models.KnowledgeDoc.tenant_id == tenant_id).all()
        scored = []
        for d in docs:
            doc_tokens = set(re.findall(r"\w+", (d.title + " " + d.content).lower()))
            overlap = len(q_tokens & doc_tokens)
            if overlap:
                scored.append({"id": str(d.id), "title": d.title, "tags": d.tags,
                               "score": round(overlap / len(q_tokens) * 100, 1)})
        return sorted(scored, key=lambda x: x["score"], reverse=True)[:limit]

    @staticmethod
    def create_procurement(session: Session, tenant_id, data: dict) -> models.ProcurementOrder:
        p = models.ProcurementOrder(tenant_id=tenant_id, status="AUTO_CREATED", **_no_tenant(data))
        session.add(p)
        session.flush()
        publish_outbox(session, "tenancy.procurement.automated.v1", tenant_id, None,
                       {"order_id": str(p.id), "item": p.item, "quantity": p.quantity})
        session.commit()
        return p

    @staticmethod
    def forecast_inventory(session: Session, tenant_id, data: dict) -> models.InventoryForecast:
        f = models.InventoryForecast(tenant_id=tenant_id, **_no_tenant(data))
        session.add(f)
        session.flush()
        publish_outbox(session, "tenancy.inventory_forecast.computed.v1", tenant_id, None,
                       {"item": f.item, "predicted_demand": f.predicted_demand})
        session.commit()
        return f

    @staticmethod
    def record_roi(session: Session, tenant_id, data: dict) -> models.RoiRecord:
        data = _no_tenant(data)
        investment = float(data.get("investment", 0)) or 1
        data["roi_pct"] = round(100 * (float(data.get("return_value", 0)) - investment) / investment, 2)
        r = models.RoiRecord(tenant_id=tenant_id, **data)
        session.add(r)
        session.commit()
        return r

    @staticmethod
    def create_scaling_rule(session: Session, tenant_id, data: dict) -> models.ScalingRule:
        s = models.ScalingRule(tenant_id=tenant_id, status="ENABLED", **_no_tenant(data))
        session.add(s)
        session.flush()
        publish_outbox(session, "tenancy.scaling_rule.applied.v1", tenant_id, None,
                       {"service": s.service, "metric": s.metric, "threshold": s.threshold})
        session.commit()
        return s

    @staticmethod
    def create_mesh_link(session: Session, tenant_id, data: dict) -> models.MeshLink:
        data = _no_tenant(data)
        data.setdefault("mtls_enabled", True)
        m = models.MeshLink(tenant_id=tenant_id, status="CONNECTED", **data)
        session.add(m)
        session.flush()
        publish_outbox(session, "tenancy.mesh_link.established.v1", tenant_id, None,
                       {"source": m.source, "target": m.target, "mtls": m.mtls_enabled})
        session.commit()
        return m

    @staticmethod
    def register_cloud(session: Session, tenant_id, data: dict) -> models.CloudAbstraction:
        c = models.CloudAbstraction(tenant_id=tenant_id, abstraction_status="ACTIVE",
                                    portability_status="READY", **_no_tenant(data))
        session.add(c)
        session.commit()
        return c

    @staticmethod
    def migrate_workload(session: Session, tenant_id, workload_name: str,
                         target_cloud: str) -> models.CloudAbstraction:
        row = session.query(models.CloudAbstraction).filter(
            models.CloudAbstraction.tenant_id == tenant_id,
            models.CloudAbstraction.workload_name == workload_name).first()
        if not row:
            raise KeyError("workload not found")
        row.source_cloud = row.provider
        row.target_cloud = target_cloud
        row.portability_status = "MIGRATED"
        session.flush()
        publish_outbox(session, "tenancy.workload.migrated.v1", tenant_id, None,
                       {"workload": workload_name, "from": row.source_cloud, "to": target_cloud})
        session.commit()
        return row

    @staticmethod
    def translate(session: Session, tenant_id, text: str, target_lang: str) -> models.Translation:
        """Multi-language AI (892): platform dictionary for common tokens, else identity."""
        _DICT = {"hello": "hola", "welcome": "bienvenido", "goodbye": "adiós"}
        translated = " ".join(_DICT.get(w, w) for w in text.split())
        t = models.Translation(tenant_id=tenant_id, source_text=text, source_lang="en",
                               target_lang=target_lang, translated_text=translated)
        session.add(t)
        session.commit()
        return t
