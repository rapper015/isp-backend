"""Intelligence operations services (Master Spec Batch 7b — aiops P0).

Personalization, bottleneck detection, automation coverage, node/region
profitability. Signals never mutate domain state — they record insights.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (AutomationCoverage, Bottleneck, NodeProfit,
                      PersonalizationProfile, RegionProfitability)
from .audit_service import audit, outbox


def _now():
    return datetime.now(timezone.utc)


def _no_tenant(data: dict) -> dict:
    return {k: v for k, v in data.items() if k != "tenant_id"}


class PersonalizationService:
    @staticmethod
    def upsert(session: Session, tenant_id, data: dict, actor="system") -> PersonalizationProfile:
        row = session.scalar(select(PersonalizationProfile).where(
            PersonalizationProfile.tenant_id == tenant_id,
            PersonalizationProfile.subscriber_id == data["subscriber_id"]))
        if row:
            row.segments = data.get("segments", row.segments)
            row.preferences = data.get("preferences", row.preferences)
            row.engagement_score = float(data.get("engagement_score", row.engagement_score))
        else:
            row = PersonalizationProfile(tenant_id=tenant_id, **_no_tenant(data))
            session.add(row)
        session.flush()
        outbox(session, "ai.personalization.updated.v1", tenant_id, None,
               {"subscriber_id": row.subscriber_id, "segments": row.segments})
        session.commit()
        return row

    @staticmethod
    def recommend(session: Session, tenant_id, subscriber_id: str) -> dict:
        """Deep personalization (889): segment-matched recommendation."""
        row = session.scalar(select(PersonalizationProfile).where(
            PersonalizationProfile.tenant_id == tenant_id,
            PersonalizationProfile.subscriber_id == subscriber_id))
        if not row:
            return {"subscriber_id": subscriber_id, "recommendation": None}
        segments = row.segments or []
        if "PREMIUM" in segments:
            rec = "offer_fiber_500_plus_ott"
        elif "DATA_HEAVY" in segments:
            rec = "offer_unlimited_data_addon"
        else:
            rec = "offer_standard_plan_upgrade"
        return {"subscriber_id": subscriber_id, "segments": segments,
                "recommendation": rec, "engagement_score": row.engagement_score}


class BottleneckService:
    @staticmethod
    def detect(session: Session, tenant_id, scope: str, metric: str, value: float,
               threshold: float, actor="system") -> Bottleneck | None:
        """System Bottleneck Detector (1289)."""
        if value < threshold:
            return None
        row = Bottleneck(tenant_id=tenant_id, scope=scope, metric=metric,
                         severity="HIGH" if value > threshold * 1.5 else "MEDIUM",
                         status="OPEN", detected_at=_now())
        session.add(row)
        session.flush()
        outbox(session, "ai.bottleneck.detected.v1", tenant_id, None,
               {"scope": scope, "metric": metric, "value": value, "threshold": threshold})
        session.commit()
        return row

    @staticmethod
    def resolve(session: Session, tenant_id, bottleneck_id: uuid.UUID) -> Bottleneck:
        row = session.scalar(select(Bottleneck).where(
            Bottleneck.id == bottleneck_id, Bottleneck.tenant_id == tenant_id))
        if not row:
            raise KeyError("bottleneck not found")
        row.status = "RESOLVED"
        session.commit()
        return row


class CoverageService:
    @staticmethod
    def compute(session: Session, tenant_id, period: str, automated: int, manual: int,
                actor="system") -> AutomationCoverage:
        """Automation Coverage Tracking (1297): % automated vs manual."""
        total = automated + manual
        pct = round(100 * automated / total, 2) if total else 0.0
        row = session.scalar(select(AutomationCoverage).where(
            AutomationCoverage.tenant_id == tenant_id,
            AutomationCoverage.period == period))
        if row:
            row.automated_count, row.manual_count, row.coverage_pct = automated, manual, pct
        else:
            row = AutomationCoverage(tenant_id=tenant_id, period=period,
                                     automated_count=automated, manual_count=manual,
                                     coverage_pct=pct)
            session.add(row)
        session.flush()
        outbox(session, "ai.automation_coverage.computed.v1", tenant_id, None,
               {"period": period, "coverage_pct": pct})
        session.commit()
        return row


class ProfitabilityService:
    @staticmethod
    def node_profit(session: Session, tenant_id, data: dict, actor="system") -> NodeProfit:
        """Profit per Node (1420)."""
        data = _no_tenant(data)
        data["profit"] = round(float(data.get("revenue", 0)) - float(data.get("cost", 0)), 2)
        row = session.scalar(select(NodeProfit).where(
            NodeProfit.tenant_id == tenant_id, NodeProfit.node == data["node"],
            NodeProfit.period == data.get("period", "MONTH")))
        if row:
            row.revenue, row.cost, row.profit = data["revenue"], data["cost"], data["profit"]
        else:
            row = NodeProfit(tenant_id=tenant_id, **data)
            session.add(row)
        session.flush()
        outbox(session, "ai.node_profit.recorded.v1", tenant_id, None,
               {"node": row.node, "profit": row.profit})
        session.commit()
        return row

    @staticmethod
    def region_profitability(session: Session, tenant_id, data: dict, actor="system") -> RegionProfitability:
        """Region Profitability Analysis (1481)."""
        data = _no_tenant(data)
        revenue = float(data.get("revenue", 0))
        cost = float(data.get("cost", 0))
        data["profit_margin"] = round(100 * (revenue - cost) / revenue, 2) if revenue else 0.0
        row = session.scalar(select(RegionProfitability).where(
            RegionProfitability.tenant_id == tenant_id, RegionProfitability.region == data["region"],
            RegionProfitability.period == data.get("period", "MONTH")))
        if row:
            row.revenue, row.cost, row.profit_margin = revenue, cost, data["profit_margin"]
        else:
            row = RegionProfitability(tenant_id=tenant_id, **data)
            session.add(row)
        session.flush()
        outbox(session, "ai.region_profitability.recorded.v1", tenant_id, None,
               {"region": row.region, "profit_margin": row.profit_margin})
        session.commit()
        return row
