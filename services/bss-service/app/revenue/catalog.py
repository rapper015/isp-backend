"""BSS monetization & catalog services (Master Spec Batch 5)."""
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from . import models
from .events import publish_outbox


def _now():
    return datetime.now(timezone.utc)


def _no_tenant(data: dict) -> dict:
    return {k: v for k, v in data.items() if k != "tenant_id"}


def _wallet_balance(session: Session, tenant_id, wallet_id: str) -> Decimal:
    """Deterministic balance = sum(credits) - sum(debits) over the ledger."""
    rows = session.query(models.WalletLedger).filter(
        models.WalletLedger.tenant_id == tenant_id,
        models.WalletLedger.wallet_id == wallet_id).all()
    balance = Decimal("0.00")
    for r in rows:
        balance += r.amount if r.entry_type == "CREDIT" else -r.amount
    return balance


def _tenant(session: Session, tenant_id):
    t = session.get(models.Tenant, tenant_id)
    if t is None:
        from fastapi import HTTPException
        raise HTTPException(404, "tenant not found")
    return t


class CatalogService:
    @staticmethod
    def create_bundle(session, tenant_id, data: dict) -> models.ProductBundle:
        _tenant(session, tenant_id)
        b = models.ProductBundle(tenant_id=tenant_id, status="ACTIVE", **_no_tenant(data))
        session.add(b)
        session.flush()
        publish_outbox(session, "catalog.bundle.created.v1",
                       {"bundle_code": b.bundle_code, "name": b.name, "items": b.items},
                       tenant_id=tenant_id)
        session.commit()
        return b

    @staticmethod
    def create_service_item(session, tenant_id, data: dict) -> models.ServiceCatalogItem:
        _tenant(session, tenant_id)
        i = models.ServiceCatalogItem(tenant_id=tenant_id, status="ACTIVE", **_no_tenant(data))
        session.add(i)
        session.flush()
        publish_outbox(session, "catalog.service_defined.v1",
                       {"code": i.code, "name": i.name, "kind": i.kind}, tenant_id=tenant_id)
        session.commit()
        return i

    @staticmethod
    def sunset_product(session, tenant_id, code: str) -> models.ServiceCatalogItem:
        """Product Sunset (1046): retire a catalog product."""
        item = session.query(models.ServiceCatalogItem).filter(
            models.ServiceCatalogItem.tenant_id == tenant_id,
            models.ServiceCatalogItem.code == code).first()
        if not item:
            raise KeyError("catalog item not found")
        item.status = "RETIRED"
        session.flush()
        publish_outbox(session, "catalog.product_sunset.v1",
                       {"code": code, "name": item.name}, tenant_id=tenant_id)
        session.commit()
        return item

    @staticmethod
    def create_enterprise_item(session, tenant_id, data: dict) -> models.EnterpriseCatalogItem:
        _tenant(session, tenant_id)
        i = models.EnterpriseCatalogItem(tenant_id=tenant_id, status="ACTIVE", **_no_tenant(data))
        session.add(i)
        session.flush()
        publish_outbox(session, "catalog.enterprise_catalog_created.v1",
                       {"code": i.code, "name": i.name, "vendor": i.vendor}, tenant_id=tenant_id)
        session.commit()
        return i

    @staticmethod
    def onboard_vendor(session, tenant_id, data: dict) -> models.Vendor:
        _tenant(session, tenant_id)
        v = models.Vendor(tenant_id=tenant_id, status="ACTIVE", **_no_tenant(data))
        session.add(v)
        session.flush()
        publish_outbox(session, "catalog.vendor_onboarded.v1",
                       {"vendor_id": str(v.id), "name": v.name, "sla_minutes": v.sla_minutes},
                       tenant_id=tenant_id)
        session.commit()
        return v

    @staticmethod
    def create_sla_tier(session, tenant_id, data: dict) -> models.SlaPricingTier:
        _tenant(session, tenant_id)
        t = models.SlaPricingTier(tenant_id=tenant_id, **_no_tenant(data))
        session.add(t)
        session.commit()
        return t

    @staticmethod
    def price_sla(session, tenant_id, base_price: Decimal, tier: str) -> dict:
        """SLA Pricing (811) + Penalty Rules (812)."""
        t = session.query(models.SlaPricingTier).filter(
            models.SlaPricingTier.tenant_id == tenant_id,
            models.SlaPricingTier.tier == tier).first()
        multiplier = t.price_multiplier if t else 1.0
        penalty_pct = t.penalty_pct if t else 0.0
        _q = Decimal("0.01")
        priced = (Decimal(str(base_price)) * Decimal(str(multiplier))).quantize(_q)
        penalty = (priced * Decimal(str(penalty_pct)) / Decimal("100")).quantize(_q)
        return {"tier": tier, "base_price": str(base_price), "priced_amount": str(priced),
                "penalty_amount": str(penalty)}

    @staticmethod
    def publish_api_product(session, tenant_id, data: dict) -> models.ApiMarketplaceProduct:
        _tenant(session, tenant_id)
        p = models.ApiMarketplaceProduct(tenant_id=tenant_id, status="PUBLISHED", **_no_tenant(data))
        session.add(p)
        session.commit()
        return p


class MonetizationService:
    @staticmethod
    def calculate_commission(session, tenant_id, reseller_id: str, gross_sales: Decimal,
                             rate: float, period: str = "MONTH", earning_code: str = "SALES") -> models.CommissionRecord:
        _tenant(session, tenant_id)
        amount = Decimal(str(gross_sales)) * Decimal(str(rate)) / Decimal("100")
        rec = session.query(models.CommissionRecord).filter(
            models.CommissionRecord.tenant_id == tenant_id,
            models.CommissionRecord.reseller_id == reseller_id,
            models.CommissionRecord.period == period,
            models.CommissionRecord.earning_code == earning_code).first()
        if rec:
            rec.gross_sales, rec.commission_rate = Decimal(str(gross_sales)), rate
            rec.commission_amount = amount
        else:
            rec = models.CommissionRecord(tenant_id=tenant_id, reseller_id=reseller_id,
                                          period=period, earning_code=earning_code,
                                          gross_sales=Decimal(str(gross_sales)),
                                          commission_rate=rate, commission_amount=amount,
                                          status="CALCULATED")
            session.add(rec)
        session.flush()
        publish_outbox(session, "monetization.commission_calculated.v1",
                       {"reseller_id": reseller_id, "amount": str(amount), "period": period},
                       tenant_id=tenant_id)
        session.commit()
        return rec

    @staticmethod
    def wallet_deduct(session, tenant_id, wallet_id: str, amount: Decimal,
                      reason: str | None = None) -> models.WalletLedger:
        """Wallet Deduction (373): debit with running balance (prepaid)."""
        _tenant(session, tenant_id)
        balance = _wallet_balance(session, tenant_id, wallet_id) - Decimal(str(amount))
        if balance < 0:
            raise ValueError("INSUFFICIENT_WALLET_BALANCE")
        entry = models.WalletLedger(tenant_id=tenant_id, wallet_id=wallet_id, entry_type="DEBIT",
                                    amount=Decimal(str(amount)), balance=balance, reason=reason)
        session.add(entry)
        session.flush()
        publish_outbox(session, "monetization.wallet_debited.v1",
                       {"wallet_id": wallet_id, "amount": str(amount), "balance": str(balance)},
                       tenant_id=tenant_id)
        session.commit()
        return entry

    @staticmethod
    def wallet_credit(session, tenant_id, wallet_id: str, amount: Decimal,
                      reason: str | None = None) -> models.WalletLedger:
        _tenant(session, tenant_id)
        balance = _wallet_balance(session, tenant_id, wallet_id) + Decimal(str(amount))
        entry = models.WalletLedger(tenant_id=tenant_id, wallet_id=wallet_id, entry_type="CREDIT",
                                    amount=Decimal(str(amount)), balance=balance, reason=reason)
        session.add(entry)
        session.commit()
        return entry

    @staticmethod
    def create_budget(session, tenant_id, data: dict) -> models.BudgetPlan:
        _tenant(session, tenant_id)
        b = models.BudgetPlan(tenant_id=tenant_id, spent_amount=Decimal("0.00"),
                              status="ACTIVE", **_no_tenant(data))
        session.add(b)
        session.commit()
        return b

    @staticmethod
    def spend_budget(session, tenant_id, budget_id: uuid.UUID, amount: Decimal) -> models.BudgetPlan:
        b = session.query(models.BudgetPlan).filter(
            models.BudgetPlan.id == budget_id, models.BudgetPlan.tenant_id == tenant_id).first()
        if not b:
            raise KeyError("budget not found")
        b.spent_amount += Decimal(str(amount))
        if b.spent_amount > b.limit_amount:
            raise ValueError("BUDGET_EXCEEDED")
        session.commit()
        return b

    @staticmethod
    def create_cost_center(session, tenant_id, data: dict) -> models.CostCenter:
        _tenant(session, tenant_id)
        c = models.CostCenter(tenant_id=tenant_id, **_no_tenant(data))
        session.add(c)
        session.commit()
        return c

    @staticmethod
    def create_profit_center(session, tenant_id, data: dict) -> models.ProfitCenter:
        _tenant(session, tenant_id)
        p = models.ProfitCenter(tenant_id=tenant_id, **_no_tenant(data))
        session.add(p)
        session.commit()
        return p

    @staticmethod
    def record_feature_adoption(session, tenant_id, feature: str, subscribers: int,
                                usage: int, period: str = "MONTH") -> models.FeatureAdoption:
        _tenant(session, tenant_id)
        row = session.query(models.FeatureAdoption).filter(
            models.FeatureAdoption.tenant_id == tenant_id,
            models.FeatureAdoption.feature == feature,
            models.FeatureAdoption.period == period).first()
        if row:
            row.subscriber_count, row.usage_count = subscribers, usage
        else:
            row = models.FeatureAdoption(tenant_id=tenant_id, feature=feature, period=period,
                                         subscriber_count=subscribers, usage_count=usage)
            session.add(row)
        session.commit()
        return row

    @staticmethod
    def partner_sla_analytics(session, tenant_id, partner: str, sla_pct: float,
                              breaches: int, period: str = "MONTH") -> models.PartnerSlaMetric:
        _tenant(session, tenant_id)
        row = session.query(models.PartnerSlaMetric).filter(
            models.PartnerSlaMetric.tenant_id == tenant_id,
            models.PartnerSlaMetric.partner == partner,
            models.PartnerSlaMetric.period == period).first()
        if row:
            row.sla_pct, row.breaches = sla_pct, breaches
        else:
            row = models.PartnerSlaMetric(tenant_id=tenant_id, partner=partner, period=period,
                                          sla_pct=sla_pct, breaches=breaches)
            session.add(row)
        session.commit()
        return row

    @staticmethod
    def track_churn(session, tenant_id, subscriber_id: str, stage: str,
                    reason: str | None = None) -> models.ChurnRecord:
        _tenant(session, tenant_id)
        row = session.query(models.ChurnRecord).filter(
            models.ChurnRecord.tenant_id == tenant_id,
            models.ChurnRecord.subscriber_id == subscriber_id).first()
        if row:
            row.stage, row.reason = stage, reason
            if stage == "CHURNED":
                row.churned_at = _now()
        else:
            row = models.ChurnRecord(tenant_id=tenant_id, subscriber_id=subscriber_id,
                                     stage=stage, reason=reason,
                                     churned_at=_now() if stage == "CHURNED" else None)
            session.add(row)
        session.flush()
        publish_outbox(session, "monetization.churn_tracked.v1",
                       {"subscriber_id": subscriber_id, "stage": stage}, tenant_id=tenant_id)
        session.commit()
        return row

    @staticmethod
    def start_trial(session, tenant_id, subscriber_id: str, plan: str) -> models.TrialRecord:
        _tenant(session, tenant_id)
        t = models.TrialRecord(tenant_id=tenant_id, subscriber_id=subscriber_id, plan=plan,
                               converted=False)
        session.add(t)
        session.commit()
        return t

    @staticmethod
    def convert_trial(session, tenant_id, trial_id: uuid.UUID) -> models.TrialRecord:
        t = session.query(models.TrialRecord).filter(
            models.TrialRecord.id == trial_id, models.TrialRecord.tenant_id == tenant_id).first()
        if not t:
            raise KeyError("trial not found")
        t.converted = True
        t.converted_at = _now()
        session.flush()
        publish_outbox(session, "monetization.trial_converted.v1",
                       {"subscriber_id": t.subscriber_id, "plan": t.plan}, tenant_id=tenant_id)
        session.commit()
        return t

    @staticmethod
    def trial_conversion_rate(session, tenant_id, period: str = "MONTH") -> dict:
        """Trial Conversion Analytics (1431)."""
        trials = session.query(models.TrialRecord).filter(
            models.TrialRecord.tenant_id == tenant_id).all()
        converted = [t for t in trials if t.converted]
        return {"trials": len(trials), "converted": len(converted),
                "conversion_rate": round(100 * len(converted) / len(trials), 2) if trials else 0.0}

    @staticmethod
    def compute_stickiness(session, tenant_id, product: str, retention_pct: float,
                           period: str = "MONTH") -> models.ProductStickiness:
        """Product Stickiness Score (1498): retention-weighted engagement."""
        _tenant(session, tenant_id)
        adoption = session.query(models.FeatureAdoption).filter(
            models.FeatureAdoption.tenant_id == tenant_id,
            models.FeatureAdoption.feature == product,
            models.FeatureAdoption.period == period).first()
        engagement = (adoption.usage_count / adoption.subscriber_count) if adoption and adoption.subscriber_count else 0.0
        score = round(0.7 * retention_pct + 0.3 * min(engagement * 100, 100), 2)
        row = session.query(models.ProductStickiness).filter(
            models.ProductStickiness.tenant_id == tenant_id,
            models.ProductStickiness.product == product,
            models.ProductStickiness.period == period).first()
        if row:
            row.retention_pct, row.stickiness_score = retention_pct, score
        else:
            row = models.ProductStickiness(tenant_id=tenant_id, product=product, period=period,
                                           retention_pct=retention_pct, stickiness_score=score)
            session.add(row)
        session.flush()
        publish_outbox(session, "monetization.stickiness_computed.v1",
                       {"product": product, "score": score}, tenant_id=tenant_id)
        session.commit()
        return row


class GrowthService:
    """Coupons, redemption, service composition, expense intelligence,
    margin optimization, viral referrals (Master Spec Batch 8)."""

    @staticmethod
    def issue_coupon(session, tenant_id, data: dict) -> models.Coupon:
        """Coupon Engine (682): create a discount coupon."""
        _tenant(session, tenant_id)
        c = models.Coupon(tenant_id=tenant_id, status="ACTIVE", **_no_tenant(data))
        session.add(c)
        session.commit()
        return c

    @staticmethod
    def apply_coupon(session, tenant_id, code: str, amount: Decimal) -> dict:
        """Coupon Engine (682): apply a coupon to an amount."""
        _tenant(session, tenant_id)
        c = session.query(models.Coupon).filter(
            models.Coupon.tenant_id == tenant_id, models.Coupon.code == code).first()
        if not c or c.status != "ACTIVE":
            raise KeyError("coupon not found or inactive")
        if c.used_count >= c.max_uses:
            raise ValueError("COUPON_EXHAUSTED")
        c.used_count += 1
        if c.discount_type == "FIXED":
            discount = Decimal(str(c.discount_value))
        else:
            discount = Decimal(str(amount)) * Decimal(str(c.discount_value)) / Decimal("100")
        discount = discount.quantize(Decimal("0.01"))
        payable = max(Decimal("0.00"), Decimal(str(amount)) - discount)
        session.flush()
        publish_outbox(session, "catalog.coupon.applied.v1",
                       {"code": code, "discount": str(discount), "payable": str(payable)},
                       tenant_id=tenant_id)
        session.commit()
        return {"code": code, "discount": str(discount), "payable": str(payable),
                "used_count": c.used_count}

    @staticmethod
    def redeem(session, tenant_id, data: dict) -> models.Redemption:
        """Redemption (690): redeem loyalty points."""
        _tenant(session, tenant_id)
        r = models.Redemption(tenant_id=tenant_id, status="REDEEMED", **_no_tenant(data))
        session.add(r)
        session.flush()
        publish_outbox(session, "catalog.points.redeemed.v1",
                       {"customer_id": r.customer_id, "points": r.points, "reward": r.reward},
                       tenant_id=tenant_id)
        session.commit()
        return r

    @staticmethod
    def compose(session, tenant_id, data: dict) -> models.ServiceComposition:
        """Dynamic Service Composition (808): compose services on demand."""
        _tenant(session, tenant_id)
        c = models.ServiceComposition(tenant_id=tenant_id, status="ACTIVE", **_no_tenant(data))
        session.add(c)
        session.flush()
        publish_outbox(session, "catalog.service.composed.v1",
                       {"composition_code": c.composition_code,
                        "components": len(c.components or []), "price": str(c.price)},
                       tenant_id=tenant_id)
        session.commit()
        return c

    @staticmethod
    def categorize_expense(session, tenant_id, data: dict) -> models.ExpenseRecord:
        """Expense Intelligence (903): AI expense categorization."""
        _tenant(session, tenant_id)
        description = (data.get("description") or "").lower()
        category = "OTHER"
        for kw, cat in (("rent", "FACILITY"), ("salary", "PAYROLL"), ("bandwidth", "NETWORK"),
                        ("electric", "UTILITIES"), ("advertis", "MARKETING"), ("legal", "LEGAL"),
                        ("software", "SOFTWARE"), ("hardware", "HARDWARE")):
            if kw in description:
                category = cat
                break
        rec = models.ExpenseRecord(tenant_id=tenant_id, category=category,
                                   confidence=0.85, **_no_tenant(data))
        session.add(rec)
        session.flush()
        publish_outbox(session, "catalog.expense.categorized.v1",
                       {"description": data.get("description"), "category": category},
                       tenant_id=tenant_id)
        session.commit()
        return rec

    @staticmethod
    def optimize_margin(session, tenant_id, data: dict) -> models.MarginOptimization:
        """Margin Optimization AI (1265): recommend pricing/mix to lift margin."""
        _tenant(session, tenant_id)
        segment = data.get("segment", "")
        period = data.get("period", "MONTH")
        current = float(data.get("current_margin_pct", 0.0))
        # heuristic: rebalance low-margin components toward the blended target
        optimized = round(min(100.0, current + max(1.0, 5.0 - current * 0.05)), 2)
        recommendation = data.get("recommendation") or (
            "Shift traffic mix to high-margin plans and renegotiate backhaul per-GB.")
        row = session.query(models.MarginOptimization).filter(
            models.MarginOptimization.tenant_id == tenant_id,
            models.MarginOptimization.segment == segment,
            models.MarginOptimization.period == period).first()
        if row:
            row.current_margin_pct = current
            row.optimized_margin_pct = optimized
            row.recommendation = recommendation
        else:
            row = models.MarginOptimization(tenant_id=tenant_id, segment=segment, period=period,
                                            current_margin_pct=current,
                                            optimized_margin_pct=optimized,
                                            recommendation=recommendation)
            session.add(row)
        session.flush()
        publish_outbox(session, "catalog.margin.improved.v1",
                       {"segment": segment, "current": current, "optimized": optimized},
                       tenant_id=tenant_id)
        session.commit()
        return row

    @staticmethod
    def trigger_referral(session, tenant_id, data: dict) -> models.Referral:
        """Viral Growth Engine (1497): referral-based acquisition."""
        _tenant(session, tenant_id)
        r = models.Referral(tenant_id=tenant_id, status="PENDING", **_no_tenant(data))
        session.add(r)
        session.flush()
        publish_outbox(session, "catalog.referral.triggered.v1",
                       {"referrer_id": r.referrer_id, "referee_id": r.referee_id,
                        "reward": str(r.reward)},
                       tenant_id=tenant_id)
        session.commit()
        return r
