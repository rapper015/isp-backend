"""Warehouse ORM models (`wh_` prefix; Master Spec Batch 7d)."""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, Index, Integer, JSON, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class Kpi(Base, Timestamped):
    __tablename__ = "wh_kpi"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_wh_kpi"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True, nullable=False)
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(60), default="BUSINESS")
    target: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str] = mapped_column(String(30), default="COUNT")
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")


class RevenueTrend(Base, Timestamped):
    __tablename__ = "wh_revenue_trend"
    __table_args__ = (UniqueConstraint("tenant_id", "stream", "period", name="uq_wh_revenue_trend"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True, nullable=False)
    stream: Mapped[str] = mapped_column(String(120), nullable=False)
    period: Mapped[str] = mapped_column(String(20), default="MONTH")
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    trend: Mapped[float] = mapped_column(Float, default=0.0)  # % vs prior period


class Profitability(Base, Timestamped):
    __tablename__ = "wh_profitability"
    __table_args__ = (UniqueConstraint("tenant_id", "segment", "period", name="uq_wh_profitability"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True, nullable=False)
    segment: Mapped[str] = mapped_column(String(120), nullable=False)
    period: Mapped[str] = mapped_column(String(20), default="MONTH")
    revenue: Mapped[float] = mapped_column(Float, default=0.0)
    cost: Mapped[float] = mapped_column(Float, default=0.0)
    margin_pct: Mapped[float] = mapped_column(Float, default=0.0)


class AnalyticsCluster(Base, Timestamped):
    __tablename__ = "wh_analytics_cluster"
    __table_args__ = (UniqueConstraint("tenant_id", "node", name="uq_wh_cluster_node"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True, nullable=False)
    node: Mapped[str] = mapped_column(String(160), nullable=False)
    role: Mapped[str] = mapped_column(String(40), default="WORKER")
    status: Mapped[str] = mapped_column(String(20), default="READY")
    load: Mapped[float] = mapped_column(Float, default=0.0)


class EcosystemMetric(Base, Timestamped):
    __tablename__ = "wh_ecosystem_metric"
    __table_args__ = (UniqueConstraint("tenant_id", "partner", "period", name="uq_wh_ecosystem"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True, nullable=False)
    partner: Mapped[str] = mapped_column(String(160), nullable=False)
    period: Mapped[str] = mapped_column(String(20), default="MONTH")
    metric: Mapped[str] = mapped_column(String(80), nullable=False)
    value: Mapped[float] = mapped_column(Float, default=0.0)


class ScenarioComparison(Base, Timestamped):
    """Scenario Comparison Engine (feature 1340): compare simulations."""
    __tablename__ = "wh_scenario_comparison"
    __table_args__ = (UniqueConstraint("tenant_id", "comparison_name", name="uq_wh_scenario_comp"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True, nullable=False)
    comparison_name: Mapped[str] = mapped_column(String(160), nullable=False)
    baseline: Mapped[dict] = mapped_column(JSON, default=dict)
    alternatives: Mapped[list] = mapped_column(JSON, default=list)  # [{"name": ..., "metrics": {...}, "delta": {...}}]
    winner: Mapped[str | None] = mapped_column(String(160))
