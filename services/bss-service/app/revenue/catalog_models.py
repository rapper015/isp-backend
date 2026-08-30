"""BSS monetization & catalog models (Master Spec Batch 5).

Covers: 104 bundles, 368 commission calc, 373 wallet deduction, 680 service
catalog, 802 enterprise catalog, 803 vendor onboarding, 804 vendor SLA, 811 SLA
pricing, 812 penalty rules, 816 API marketplace, 905 budget planning, 1009
logical service catalog, 1046 product sunset, 1221 profit centers, 1222 cost
centers, 1249 feature adoption, 1293 partner SLA analytics, 1430 churn
lifecycle, 1431 trial conversion, 1498 product stickiness.
"""
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class ProductBundle(Base, Timestamped):
    __tablename__ = "bss_product_bundles"
    __table_args__ = (UniqueConstraint("tenant_id", "bundle_code", name="uq_bss_bundle_code"),
                      Index("ix_bss_bundle_tenant", "tenant_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_tenants.id"), index=True, nullable=False)
    bundle_code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    items: Mapped[list] = mapped_column(JSON, default=list)
    monthly_fee: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")


class ServiceCatalogItem(Base, Timestamped):
    __tablename__ = "bss_service_catalog"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_bss_catalog_code"),
                      Index("ix_bss_catalog_tenant", "tenant_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_tenants.id"), index=True, nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), default="SERVICE")  # SERVICE | PRODUCT
    logical_def: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")  # ACTIVE | RETIRED


class EnterpriseCatalogItem(Base, Timestamped):
    __tablename__ = "bss_enterprise_catalog"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_bss_ent_catalog_code"),
                      Index("ix_bss_ent_catalog_tenant", "tenant_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_tenants.id"), index=True, nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    vendor: Mapped[str] = mapped_column(String(160), nullable=False)
    terms: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")


class Vendor(Base, Timestamped):
    __tablename__ = "bss_vendors"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_bss_vendor_name"),
                      Index("ix_bss_vendor_tenant", "tenant_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_tenants.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    sla_minutes: Mapped[int] = mapped_column(Integer, default=480)
    penalty_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")


class SlaPricingTier(Base, Timestamped):
    __tablename__ = "bss_sla_pricing_tier"
    __table_args__ = (UniqueConstraint("tenant_id", "tier", name="uq_bss_sla_tier"),
                      Index("ix_bss_sla_tier_tenant", "tenant_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_tenants.id"), index=True, nullable=False)
    tier: Mapped[str] = mapped_column(String(40), nullable=False)  # GOLD | SILVER | BRONZE
    price_multiplier: Mapped[float] = mapped_column(Float, default=1.0)
    penalty_pct: Mapped[float] = mapped_column(Float, default=0.0)


class ApiMarketplaceProduct(Base, Timestamped):
    __tablename__ = "bss_api_marketplace"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_bss_api_product"),
                      Index("ix_bss_api_product_tenant", "tenant_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_tenants.id"), index=True, nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    price_per_call: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=Decimal("0.01"))
    tier: Mapped[str] = mapped_column(String(30), default="STANDARD")
    status: Mapped[str] = mapped_column(String(20), default="PUBLISHED")


class BudgetPlan(Base, Timestamped):
    __tablename__ = "bss_budget_plans"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_bss_budget_name"),
                      Index("ix_bss_budget_tenant", "tenant_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_tenants.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    period: Mapped[str] = mapped_column(String(20), default="YEAR")
    limit_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    spent_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"))
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")


class CostCenter(Base, Timestamped):
    __tablename__ = "bss_cost_centers"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_bss_cost_center_code"),
                      Index("ix_bss_cost_center_tenant", "tenant_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_tenants.id"), index=True, nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    budget: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"))
    spent: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"))


class ProfitCenter(Base, Timestamped):
    __tablename__ = "bss_profit_centers"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_bss_profit_center_code"),
                      Index("ix_bss_profit_center_tenant", "tenant_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_tenants.id"), index=True, nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    target: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"))
    achieved: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"))


class FeatureAdoption(Base, Timestamped):
    __tablename__ = "bss_feature_adoption"
    __table_args__ = (UniqueConstraint("tenant_id", "feature", "period", name="uq_bss_feature_adopt"),
                      Index("ix_bss_feature_adopt_tenant", "tenant_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_tenants.id"), index=True, nullable=False)
    feature: Mapped[str] = mapped_column(String(160), nullable=False)
    subscriber_count: Mapped[int] = mapped_column(Integer, default=0)
    usage_count: Mapped[int] = mapped_column(Integer, default=0)
    period: Mapped[str] = mapped_column(String(20), default="MONTH")


class PartnerSlaMetric(Base, Timestamped):
    __tablename__ = "bss_partner_sla_metric"
    __table_args__ = (UniqueConstraint("tenant_id", "partner", "period", name="uq_bss_partner_sla"),
                      Index("ix_bss_partner_sla_tenant", "tenant_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_tenants.id"), index=True, nullable=False)
    partner: Mapped[str] = mapped_column(String(160), nullable=False)
    period: Mapped[str] = mapped_column(String(20), default="MONTH")
    sla_pct: Mapped[float] = mapped_column(Float, default=100.0)
    breaches: Mapped[int] = mapped_column(Integer, default=0)


class ChurnRecord(Base, Timestamped):
    __tablename__ = "bss_churn_records"
    __table_args__ = (UniqueConstraint("tenant_id", "subscriber_id", name="uq_bss_churn_sub"),
                      Index("ix_bss_churn_tenant", "tenant_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_tenants.id"), index=True, nullable=False)
    subscriber_id: Mapped[str] = mapped_column(String(128), nullable=False)
    stage: Mapped[str] = mapped_column(String(30), default="AT_RISK")  # AT_RISK | CHURNED | WON_BACK
    reason: Mapped[str | None] = mapped_column(Text)
    churned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TrialRecord(Base, Timestamped):
    __tablename__ = "bss_trial_records"
    __table_args__ = (UniqueConstraint("tenant_id", "subscriber_id", "plan", name="uq_bss_trial_sub"),
                      Index("ix_bss_trial_tenant", "tenant_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_tenants.id"), index=True, nullable=False)
    subscriber_id: Mapped[str] = mapped_column(String(128), nullable=False)
    plan: Mapped[str] = mapped_column(String(120), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    converted: Mapped[bool] = mapped_column(Boolean, default=False)
    converted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProductStickiness(Base, Timestamped):
    __tablename__ = "bss_product_stickiness"
    __table_args__ = (UniqueConstraint("tenant_id", "product", "period", name="uq_bss_stickiness"),
                      Index("ix_bss_stickiness_tenant", "tenant_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_tenants.id"), index=True, nullable=False)
    product: Mapped[str] = mapped_column(String(160), nullable=False)
    period: Mapped[str] = mapped_column(String(20), default="MONTH")
    retention_pct: Mapped[float] = mapped_column(Float, default=0.0)
    stickiness_score: Mapped[float] = mapped_column(Float, default=0.0)


class CommissionRecord(Base, Timestamped):
    __tablename__ = "bss_commission_records"
    __table_args__ = (UniqueConstraint("tenant_id", "reseller_id", "period", "earning_code", name="uq_bss_commission"),
                      Index("ix_bss_commission_tenant", "tenant_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_tenants.id"), index=True, nullable=False)
    reseller_id: Mapped[str] = mapped_column(String(128), nullable=False)
    period: Mapped[str] = mapped_column(String(20), default="MONTH")
    earning_code: Mapped[str] = mapped_column(String(40), default="SALES")
    gross_sales: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    commission_rate: Mapped[float] = mapped_column(Float, default=0.0)
    commission_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"))
    status: Mapped[str] = mapped_column(String(20), default="CALCULATED")


class WalletLedger(Base, Timestamped):
    __tablename__ = "bss_wallet_ledger"
    __table_args__ = (Index("ix_bss_wallet_tenant", "tenant_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_tenants.id"), index=True, nullable=False)
    wallet_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    entry_type: Mapped[str] = mapped_column(String(20), nullable=False)  # CREDIT | DEBIT
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    balance: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(200))


# --- Master Spec Batch 8f: coupons, redemption, service composition, expense
# intelligence, margin optimization, viral growth ---

class Coupon(Base, Timestamped):
    """Coupon engine (feature 682): discount coupons."""
    __tablename__ = "bss_coupon"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_bss_coupon"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_tenants.id"), index=True, nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    discount_type: Mapped[str] = mapped_column(String(20), default="PERCENT")  # PERCENT | FIXED
    discount_value: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    max_uses: Mapped[int] = mapped_column(Integer, default=1)
    used_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Redemption(Base, Timestamped):
    """Redemption (feature 690): redeem loyalty points for rewards."""
    __tablename__ = "bss_redemption"
    __table_args__ = (Index("ix_bss_redemption_tenant", "tenant_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_tenants.id"), index=True, nullable=False)
    customer_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    points: Mapped[int] = mapped_column(Integer, default=0)
    reward: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")  # PENDING -> REDEEMED


class ServiceComposition(Base, Timestamped):
    """Dynamic service composition (feature 808)."""
    __tablename__ = "bss_service_composition"
    __table_args__ = (UniqueConstraint("tenant_id", "composition_code", name="uq_bss_composition"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_tenants.id"), index=True, nullable=False)
    composition_code: Mapped[str] = mapped_column(String(64), nullable=False)
    components: Mapped[list] = mapped_column(JSON, default=list)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")


class ExpenseRecord(Base, Timestamped):
    """Expense intelligence (feature 903): AI expense categorization."""
    __tablename__ = "bss_expense_record"
    __table_args__ = (Index("ix_bss_expense_tenant", "tenant_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_tenants.id"), index=True, nullable=False)
    description: Mapped[str] = mapped_column(String(300), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    category: Mapped[str] = mapped_column(String(80), default="OTHER")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)


class MarginOptimization(Base, Timestamped):
    """Margin optimization AI (feature 1265)."""
    __tablename__ = "bss_margin_optimization"
    __table_args__ = (UniqueConstraint("tenant_id", "segment", "period", name="uq_bss_margin_opt"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_tenants.id"), index=True, nullable=False)
    segment: Mapped[str] = mapped_column(String(120), nullable=False)
    period: Mapped[str] = mapped_column(String(20), default="MONTH")
    current_margin_pct: Mapped[float] = mapped_column(Float, default=0.0)
    optimized_margin_pct: Mapped[float] = mapped_column(Float, default=0.0)
    recommendation: Mapped[str | None] = mapped_column(Text)


class Referral(Base, Timestamped):
    """Viral growth engine (feature 1497): referral-based acquisition."""
    __tablename__ = "bss_referral"
    __table_args__ = (Index("ix_bss_referral_tenant", "tenant_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bss_tenants.id"), index=True, nullable=False)
    referrer_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    referee_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    reward: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    status: Mapped[str] = mapped_column(String(20), default="PENDING")  # PENDING -> CREDITED
