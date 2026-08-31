"""Intelligence operations models (Master Spec Batch 7b — aiops P0).

Covers: 889 personalization v2, 1289 system bottleneck detector, 1297
automation coverage, 1420 profit per node, 1481 region profitability.
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Timestamped, UuidPk
from ..database import Base


class PersonalizationProfile(UuidPk, Base, Timestamped):
    __tablename__ = "ai_personalization_profile"
    __table_args__ = (UniqueConstraint("tenant_id", "subscriber_id", name="uq_ai_personalization"),
                      Index("ix_ai_personalization_tenant", "tenant_id"),)
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True, nullable=False)
    subscriber_id: Mapped[str] = mapped_column(String(128), nullable=False)
    segments: Mapped[list] = mapped_column(JSON, default=list)
    preferences: Mapped[dict] = mapped_column(JSON, default=dict)
    engagement_score: Mapped[float] = mapped_column(Float, default=0.0)


class Bottleneck(UuidPk, Base, Timestamped):
    __tablename__ = "ai_bottleneck"
    __table_args__ = (Index("ix_ai_bottleneck_tenant", "tenant_id"),)
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True, nullable=False)
    scope: Mapped[str] = mapped_column(String(160), nullable=False)
    metric: Mapped[str] = mapped_column(String(80), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), default="MEDIUM")
    status: Mapped[str] = mapped_column(String(20), default="OPEN")
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc))


class AutomationCoverage(UuidPk, Base, Timestamped):
    __tablename__ = "ai_automation_coverage"
    __table_args__ = (UniqueConstraint("tenant_id", "period", name="uq_ai_automation"),
                      Index("ix_ai_automation_tenant", "tenant_id"),)
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True, nullable=False)
    period: Mapped[str] = mapped_column(String(20), default="MONTH")
    automated_count: Mapped[int] = mapped_column(Integer, default=0)
    manual_count: Mapped[int] = mapped_column(Integer, default=0)
    coverage_pct: Mapped[float] = mapped_column(Float, default=0.0)


class NodeProfit(UuidPk, Base, Timestamped):
    __tablename__ = "ai_node_profit"
    __table_args__ = (UniqueConstraint("tenant_id", "node", "period", name="uq_ai_node_profit"),
                      Index("ix_ai_node_profit_tenant", "tenant_id"),)
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True, nullable=False)
    node: Mapped[str] = mapped_column(String(160), nullable=False)
    period: Mapped[str] = mapped_column(String(20), default="MONTH")
    revenue: Mapped[float] = mapped_column(Float, default=0.0)
    cost: Mapped[float] = mapped_column(Float, default=0.0)
    profit: Mapped[float] = mapped_column(Float, default=0.0)


class RegionProfitability(UuidPk, Base, Timestamped):
    __tablename__ = "ai_region_profitability"
    __table_args__ = (UniqueConstraint("tenant_id", "region", "period", name="uq_ai_region_profit"),
                      Index("ix_ai_region_profit_tenant", "tenant_id"),)
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True, nullable=False)
    region: Mapped[str] = mapped_column(String(120), nullable=False)
    period: Mapped[str] = mapped_column(String(20), default="MONTH")
    revenue: Mapped[float] = mapped_column(Float, default=0.0)
    cost: Mapped[float] = mapped_column(Float, default=0.0)
    profit_margin: Mapped[float] = mapped_column(Float, default=0.0)
