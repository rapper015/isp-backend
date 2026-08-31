"""Analytics service logic for the Warehouse service (Batch 7d)."""
import uuid

from sqlalchemy.orm import Session

from . import events
from .models import (AnalyticsCluster, EcosystemMetric, Kpi, Profitability,
                     RevenueTrend, ScenarioComparison)


def _tid(tenant_id) -> uuid.UUID:
    return uuid.UUID(str(tenant_id)) if not isinstance(tenant_id, uuid.UUID) else tenant_id


class AnalyticsService:
    # 468 KPI Management
    def upsert_kpi(self, db: Session, tenant_id, data: dict):
        t = _tid(tenant_id)
        row = db.query(Kpi).filter_by(tenant_id=t, code=data["code"]).first()
        if row is None:
            row = Kpi(tenant_id=t, code=data["code"], name=data.get("name", data["code"]))
            db.add(row)
        row.name = data.get("name", row.name)
        row.category = data.get("category", row.category or "BUSINESS")
        if "target" in data:
            row.target = float(data["target"])
        row.unit = data.get("unit", row.unit or "COUNT")
        row.status = data.get("status", row.status or "ACTIVE")
        events.outbox(db, "warehouse.kpi.updated.v1", t, {"code": row.code, "status": row.status})
        events.outbox(db, "warehouse.kpi.set.v1", t, {"code": row.code, "target": row.target})
        db.commit()
        db.refresh(row)
        return row

    # 477 Revenue Trends
    def record_revenue(self, db: Session, tenant_id, data: dict):
        t = _tid(tenant_id)
        row = db.query(RevenueTrend).filter_by(tenant_id=t, stream=data["stream"], period=data.get("period", "MONTH")).first()
        if row is None:
            row = RevenueTrend(tenant_id=t, stream=data["stream"], period=data.get("period", "MONTH"))
            db.add(row)
        row.amount = float(data.get("amount", row.amount or 0.0))
        row.trend = float(data.get("trend", row.trend or 0.0))
        events.outbox(db, "warehouse.revenue_trend.recorded.v1", t, {"stream": row.stream, "amount": row.amount})
        events.outbox(db, "warehouse.revenue.analyzed.v1", t, {"stream": row.stream, "amount": row.amount})
        db.commit()
        db.refresh(row)
        return row

    # 478 Profitability Analysis
    def record_profitability(self, db: Session, tenant_id, data: dict):
        t = _tid(tenant_id)
        revenue = float(data.get("revenue", 0.0))
        cost = float(data.get("cost", 0.0))
        margin = float(data.get("margin_pct", ((revenue - cost) / revenue * 100) if revenue else 0.0))
        row = db.query(Profitability).filter_by(tenant_id=t, segment=data["segment"], period=data.get("period", "MONTH")).first()
        if row is None:
            row = Profitability(tenant_id=t, segment=data["segment"], period=data.get("period", "MONTH"))
            db.add(row)
        row.revenue = revenue
        row.cost = cost
        row.margin_pct = margin
        events.outbox(db, "warehouse.profitability.recorded.v1", t, {"segment": row.segment, "margin_pct": row.margin_pct})
        events.outbox(db, "warehouse.profit.analyzed.v1", t, {"segment": row.segment, "margin_pct": margin})
        db.commit()
        db.refresh(row)
        return row

    # 499 Horizontal Scaling
    def scale_cluster(self, db: Session, tenant_id, data: dict):
        t = _tid(tenant_id)
        node = data["node"]
        row = db.query(AnalyticsCluster).filter_by(tenant_id=t, node=node).first()
        if row is None:
            row = AnalyticsCluster(tenant_id=t, node=node)
            db.add(row)
        row.role = data.get("role", row.role or "WORKER")
        row.status = data.get("status", row.status or "READY")
        row.load = float(data.get("load", row.load or 0.0))
        scale = int(data.get("scale", 0))
        if scale != 0 and row.status == "READY":
            row.load = max(0.0, min(100.0, row.load + scale))
            if row.load >= 80:
                row.status = "SCALING"
        events.outbox(db, "warehouse.cluster.scaled.v1", t, {"node": node, "status": row.status})
        db.commit()
        db.refresh(row)
        return row

    # 839 Ecosystem Analytics
    def record_ecosystem(self, db: Session, tenant_id, data: dict):
        t = _tid(tenant_id)
        row = db.query(EcosystemMetric).filter_by(tenant_id=t, partner=data["partner"], period=data.get("period", "MONTH"), metric=data["metric"]).first()
        if row is None:
            row = EcosystemMetric(tenant_id=t, partner=data["partner"], period=data.get("period", "MONTH"), metric=data["metric"])
            db.add(row)
        row.value = float(data.get("value", row.value or 0.0))
        events.outbox(db, "warehouse.ecosystem.recorded.v1", t, {"partner": row.partner, "metric": row.metric})
        events.outbox(db, "warehouse.ecosystem.analyzed.v1", t, {"partner": row.partner, "metric": row.metric})
        db.commit()
        db.refresh(row)
        return row

    # 1340 Scenario Comparison Engine
    def compare_scenarios(self, db: Session, tenant_id, data: dict):
        t = _tid(tenant_id)
        name = data["comparison_name"]
        baseline = data.get("baseline") or {}
        alternatives = data.get("alternatives") or []
        # pick the alternative with the best delta on the primary metric
        winner = None
        best = None
        metric = data.get("primary_metric")
        for alt in alternatives:
            delta = alt.get("delta") or {}
            if metric and metric in delta:
                score = float(delta[metric])
                if best is None or score > best:
                    best, winner = score, alt.get("name")
        row = db.query(ScenarioComparison).filter_by(tenant_id=t, comparison_name=name).first()
        if row is None:
            row = ScenarioComparison(tenant_id=t, comparison_name=name, baseline=baseline,
                                     alternatives=alternatives, winner=winner)
            db.add(row)
        else:
            row.baseline, row.alternatives, row.winner = baseline, alternatives, winner
        events.outbox(db, "warehouse.scenario.comparison.generated.v1", t,
                      {"comparison_name": name, "winner": winner})
        db.commit()
        db.refresh(row)
        return row
