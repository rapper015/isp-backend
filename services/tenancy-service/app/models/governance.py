"""Tenancy governance models (Master Spec Batch 4 — core-platform gaps).

Covers: 514 notification retry, 518 broadcast, 520 campaign scheduling, 523
campaign analytics, 525 conversion tracking, 543 delivery tracking, 630 scaling
rules, 638 service mesh, 639 mTLS, 750 product insights, 752 cloud abstraction,
754 workload portability, 759 cost optimization, 760 usage metering, 776 policy
engine v2, 779 compliance automation, 782 threat hunting, 831 service chaining,
892 multi-language AI, 920 market intelligence, 924 procurement automation, 926
inventory forecasting, 929 semantic search, 948 ROI tracking, 1389 storage cost
optimization.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, Index, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Timestamped, UuidPk
from ..database import Base


def _now():
    return datetime.now(timezone.utc)


class Notification(UuidPk, Base, Timestamped):
    __tablename__ = "ten_notification"
    __table_args__ = (Index("ix_ten_notif_tenant_status", "tenant_id", "status"),)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ten_tenants.id"), index=True, nullable=False)
    recipient: Mapped[str] = mapped_column(String(200), nullable=False)
    channel: Mapped[str] = mapped_column(String(20), default="EMAIL")  # EMAIL | SMS | PUSH | WHATSAPP
    template: Mapped[str | None] = mapped_column(String(200))
    body: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    last_error: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Campaign(UuidPk, Base, Timestamped):
    __tablename__ = "ten_campaign"
    __table_args__ = (Index("ix_ten_campaign_tenant", "tenant_id"),)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ten_tenants.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    channel: Mapped[str] = mapped_column(String(20), default="EMAIL")
    audience: Mapped[list] = mapped_column(JSON, default=list)
    schedule_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="DRAFT")  # DRAFT|SCHEDULED|RUNNING|COMPLETED
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CampaignRecipient(UuidPk, Base, Timestamped):
    __tablename__ = "ten_campaign_recipient"
    __table_args__ = (Index("ix_ten_camp_recipient", "campaign_id"),)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ten_tenants.id"), index=True, nullable=False)
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ten_campaign.id"), nullable=False)
    recipient: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="SENT")  # SENT|OPENED|CLICKED|CONVERTED
    converted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CampaignMetric(UuidPk, Base, Timestamped):
    __tablename__ = "ten_campaign_metric"
    __table_args__ = (Index("ix_ten_camp_metric", "campaign_id"),)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ten_tenants.id"), index=True, nullable=False)
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ten_campaign.id"), nullable=False)
    sent_count: Mapped[int] = mapped_column(Integer, default=0)
    opened_count: Mapped[int] = mapped_column(Integer, default=0)
    clicked_count: Mapped[int] = mapped_column(Integer, default=0)
    converted_count: Mapped[int] = mapped_column(Integer, default=0)
    open_rate: Mapped[float] = mapped_column(Float, default=0.0)
    conversion_rate: Mapped[float] = mapped_column(Float, default=0.0)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class UsageMeter(UuidPk, Base, Timestamped):
    __tablename__ = "ten_usage_meter"
    __table_args__ = (Index("ix_ten_usage_tenant", "tenant_id"),)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ten_tenants.id"), index=True, nullable=False)
    resource: Mapped[str] = mapped_column(String(120), nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str] = mapped_column(String(30), default="COUNT")
    period: Mapped[str] = mapped_column(String(20), default="MONTH")
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class CostRecord(UuidPk, Base, Timestamped):
    __tablename__ = "ten_cost_record"
    __table_args__ = (Index("ix_ten_cost_tenant", "tenant_id"),)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ten_tenants.id"), index=True, nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False)  # COMPUTE|STORAGE|NETWORK|OTHER
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="INR")
    storage_class: Mapped[str | None] = mapped_column(String(30))
    volume_gb: Mapped[float | None] = mapped_column(Float)
    note: Mapped[str | None] = mapped_column(Text)
    period: Mapped[str] = mapped_column(String(20), default="MONTH")
    booked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class GovernancePolicy(UuidPk, Base, Timestamped):
    __tablename__ = "ten_governance_policy"
    __table_args__ = (Index("ix_ten_policy_tenant", "tenant_id"),)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ten_tenants.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    category: Mapped[str] = mapped_column(String(80), default="GOVERNANCE")
    rule_json: Mapped[dict] = mapped_column(JSON, default=dict)
    severity: Mapped[str] = mapped_column(String(20), default="HIGH")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class ComplianceCheck(UuidPk, Base, Timestamped):
    __tablename__ = "ten_compliance_check"
    __table_args__ = (Index("ix_ten_comp_tenant", "tenant_id"),)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ten_tenants.id"), index=True, nullable=False)
    check_name: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="PASS")
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ThreatHunt(UuidPk, Base, Timestamped):
    __tablename__ = "ten_threat_hunt"
    __table_args__ = (Index("ix_ten_hunt_tenant", "tenant_id"),)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ten_tenants.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    indicator: Mapped[str | None] = mapped_column(String(200))
    scope: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20), default="RUNNING")  # RUNNING|COMPLETED
    findings: Mapped[list] = mapped_column(JSON, default=list)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ServiceChain(UuidPk, Base, Timestamped):
    __tablename__ = "ten_service_chain"
    __table_args__ = (Index("ix_ten_chain_tenant", "tenant_id"),)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ten_tenants.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    services: Mapped[list] = mapped_column(JSON, default=list)  # ordered [{"service": "...", "step": 1}]
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")


class Insight(UuidPk, Base, Timestamped):
    __tablename__ = "ten_insight"
    __table_args__ = (Index("ix_ten_insight_tenant", "tenant_id"),)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ten_tenants.id"), index=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(30), default="PRODUCT")  # PRODUCT | MARKET
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class KnowledgeDoc(UuidPk, Base, Timestamped):
    __tablename__ = "ten_knowledge_doc"
    __table_args__ = (Index("ix_ten_know_tenant", "tenant_id"),)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ten_tenants.id"), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    content: Mapped[str] = mapped_column(Text)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    indexed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ProcurementOrder(UuidPk, Base, Timestamped):
    __tablename__ = "ten_procurement_order"
    __table_args__ = (Index("ix_ten_proc_tenant", "tenant_id"),)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ten_tenants.id"), index=True, nullable=False)
    vendor: Mapped[str] = mapped_column(String(160), nullable=False)
    item: Mapped[str] = mapped_column(String(160), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="AUTO_CREATED")
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class InventoryForecast(UuidPk, Base, Timestamped):
    __tablename__ = "ten_inventory_forecast"
    __table_args__ = (Index("ix_ten_forecast_tenant", "tenant_id"),)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ten_tenants.id"), index=True, nullable=False)
    item: Mapped[str] = mapped_column(String(160), nullable=False)
    predicted_demand: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    period: Mapped[str] = mapped_column(String(20), default="MONTH")
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class RoiRecord(UuidPk, Base, Timestamped):
    __tablename__ = "ten_roi_record"
    __table_args__ = (Index("ix_ten_roi_tenant", "tenant_id"),)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ten_tenants.id"), index=True, nullable=False)
    project: Mapped[str] = mapped_column(String(200), nullable=False)
    investment: Mapped[float] = mapped_column(Float, nullable=False)
    return_value: Mapped[float] = mapped_column(Float, nullable=False)
    roi_pct: Mapped[float] = mapped_column(Float, default=0.0)
    period: Mapped[str] = mapped_column(String(20), default="YEAR")
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ScalingRule(UuidPk, Base, Timestamped):
    __tablename__ = "ten_scaling_rule"
    __table_args__ = (Index("ix_ten_scale_tenant", "tenant_id"),)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ten_tenants.id"), index=True, nullable=False)
    service: Mapped[str] = mapped_column(String(120), nullable=False)
    metric: Mapped[str] = mapped_column(String(80), default="CPU")
    threshold: Mapped[float] = mapped_column(Float, default=80.0)
    min_instances: Mapped[int] = mapped_column(Integer, default=1)
    max_instances: Mapped[int] = mapped_column(Integer, default=10)
    status: Mapped[str] = mapped_column(String(20), default="ENABLED")


class MeshLink(UuidPk, Base, Timestamped):
    __tablename__ = "ten_mesh_link"
    __table_args__ = (Index("ix_ten_mesh_tenant", "tenant_id"),)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ten_tenants.id"), index=True, nullable=False)
    source: Mapped[str] = mapped_column(String(120), nullable=False)
    target: Mapped[str] = mapped_column(String(120), nullable=False)
    mtls_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(20), default="CONNECTED")


class CloudAbstraction(UuidPk, Base, Timestamped):
    __tablename__ = "ten_cloud_abstraction"
    __table_args__ = (Index("ix_ten_cloud_tenant", "tenant_id"),)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ten_tenants.id"), index=True, nullable=False)
    provider: Mapped[str] = mapped_column(String(60), nullable=False)  # AWS|AZURE|GCP|ONPREM
    region: Mapped[str | None] = mapped_column(String(80))
    abstraction_status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    workload_name: Mapped[str | None] = mapped_column(String(160))
    source_cloud: Mapped[str | None] = mapped_column(String(60))
    target_cloud: Mapped[str | None] = mapped_column(String(60))
    portability_status: Mapped[str] = mapped_column(String(20), default="READY")


class Translation(UuidPk, Base, Timestamped):
    __tablename__ = "ten_translation"
    __table_args__ = (Index("ix_ten_trans_tenant", "tenant_id"),)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ten_tenants.id"), index=True, nullable=False)
    source_text: Mapped[str] = mapped_column(Text)
    source_lang: Mapped[str] = mapped_column(String(10), default="en")
    target_lang: Mapped[str] = mapped_column(String(10), nullable=False)
    translated_text: Mapped[str] = mapped_column(Text)
    engine: Mapped[str] = mapped_column(String(30), default="platform-ai")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
