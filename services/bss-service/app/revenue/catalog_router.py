"""BSS monetization & catalog API (Master Spec Batch 5). Internal-service
authenticated and tenant-scoped, mirroring the revenue router."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..revenue.models import Tenant
from . import models
from .catalog import CatalogService, MonetizationService
from .security import internal_service_auth

router = APIRouter(prefix="/api/bss", dependencies=[Depends(internal_service_auth)])


def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _tenant_id(payload: dict) -> uuid.UUID:
    tid = payload.get("tenant_id") or payload.get("tenantId")
    if not tid:
        raise HTTPException(422, "tenant_id required")
    return uuid.UUID(str(tid))


# ---------------------------------------------------------------------------
# Catalog: bundles, services, enterprise, vendors, SLA pricing, API marketplace
# ---------------------------------------------------------------------------

@router.post("/catalog/bundles", status_code=201)
def create_bundle(payload: dict, session: Session = Depends(db)):
    try:
        b = CatalogService.create_bundle(session, _tenant_id(payload), payload)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(409, str(exc)) from exc
    return {"id": str(b.id), "bundle_code": b.bundle_code, "name": b.name,
            "items": b.items, "status": b.status}


@router.post("/catalog/services", status_code=201)
def create_service_item(payload: dict, session: Session = Depends(db)):
    i = CatalogService.create_service_item(session, _tenant_id(payload), payload)
    return {"id": str(i.id), "code": i.code, "name": i.name, "kind": i.kind, "status": i.status}


@router.get("/catalog/services")
def list_service_catalog(tenant_id: uuid.UUID, session: Session = Depends(db)):
    rows = session.scalars(select(models.ServiceCatalogItem).where(
        models.ServiceCatalogItem.tenant_id == tenant_id)).all()
    return [{"id": str(i.id), "code": i.code, "name": i.name, "kind": i.kind,
             "status": i.status, "logical_def": i.logical_def} for i in rows]


@router.post("/catalog/products/{code}/sunset")
def sunset_product(code: str, payload: dict, session: Session = Depends(db)):
    try:
        item = CatalogService.sunset_product(session, _tenant_id(payload), code)
    except KeyError:
        raise HTTPException(404, "catalog item not found")
    return {"code": item.code, "status": item.status}


@router.post("/catalog/enterprise", status_code=201)
def create_enterprise_item(payload: dict, session: Session = Depends(db)):
    i = CatalogService.create_enterprise_item(session, _tenant_id(payload), payload)
    return {"id": str(i.id), "code": i.code, "name": i.name, "vendor": i.vendor, "status": i.status}


@router.get("/catalog/enterprise")
def list_enterprise_catalog(tenant_id: uuid.UUID, session: Session = Depends(db)):
    rows = session.scalars(select(models.EnterpriseCatalogItem).where(
        models.EnterpriseCatalogItem.tenant_id == tenant_id)).all()
    return [{"id": str(i.id), "code": i.code, "name": i.name, "vendor": i.vendor,
             "terms": i.terms, "status": i.status} for i in rows]


@router.post("/catalog/vendors", status_code=201)
def onboard_vendor(payload: dict, session: Session = Depends(db)):
    v = CatalogService.onboard_vendor(session, _tenant_id(payload), payload)
    return {"id": str(v.id), "name": v.name, "sla_minutes": v.sla_minutes, "status": v.status}


@router.post("/catalog/sla-tiers", status_code=201)
def create_sla_tier(payload: dict, session: Session = Depends(db)):
    t = CatalogService.create_sla_tier(session, _tenant_id(payload), payload)
    return {"id": str(t.id), "tier": t.tier, "price_multiplier": t.price_multiplier,
            "penalty_pct": t.penalty_pct}


@router.post("/catalog/sla/price")
def price_sla(payload: dict, session: Session = Depends(db)):
    return CatalogService.price_sla(session, _tenant_id(payload),
                                    Decimal(str(payload.get("base_price", "0"))),
                                    payload.get("tier", "STANDARD"))


@router.post("/catalog/api-marketplace", status_code=201)
def publish_api_product(payload: dict, session: Session = Depends(db)):
    p = CatalogService.publish_api_product(session, _tenant_id(payload), payload)
    return {"id": str(p.id), "code": p.code, "name": p.name,
            "price_per_call": str(p.price_per_call), "status": p.status}


# ---------------------------------------------------------------------------
# Monetization: commissions, wallet, budgets, centers, adoption, churn, trials
# ---------------------------------------------------------------------------

@router.post("/monetization/commissions/calculate", status_code=201)
def calculate_commission(payload: dict, session: Session = Depends(db)):
    rec = MonetizationService.calculate_commission(
        session, _tenant_id(payload), payload.get("reseller_id"),
        Decimal(str(payload.get("gross_sales", "0"))), float(payload.get("rate", 0)),
        payload.get("period", "MONTH"), payload.get("earning_code", "SALES"))
    return {"id": str(rec.id), "reseller_id": rec.reseller_id,
            "commission_amount": str(rec.commission_amount), "status": rec.status}


@router.post("/monetization/wallets/deduct", status_code=201)
def wallet_deduct(payload: dict, session: Session = Depends(db)):
    try:
        entry = MonetizationService.wallet_deduct(
            session, _tenant_id(payload), payload.get("wallet_id"),
            Decimal(str(payload.get("amount", "0"))), payload.get("reason"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"wallet_id": entry.wallet_id, "entry_type": entry.entry_type,
            "amount": str(entry.amount), "balance": str(entry.balance)}


@router.post("/monetization/wallets/credit", status_code=201)
def wallet_credit(payload: dict, session: Session = Depends(db)):
    entry = MonetizationService.wallet_credit(
        session, _tenant_id(payload), payload.get("wallet_id"),
        Decimal(str(payload.get("amount", "0"))), payload.get("reason"))
    return {"wallet_id": entry.wallet_id, "entry_type": entry.entry_type,
            "amount": str(entry.amount), "balance": str(entry.balance)}


@router.get("/monetization/wallets/{wallet_id}/balance")
def wallet_balance(wallet_id: str, tenant_id: uuid.UUID, session: Session = Depends(db)):
    from .catalog import _wallet_balance as bal
    return {"wallet_id": wallet_id, "balance": str(bal(session, tenant_id, wallet_id))}


@router.post("/monetization/budgets", status_code=201)
def create_budget(payload: dict, session: Session = Depends(db)):
    b = MonetizationService.create_budget(session, _tenant_id(payload), payload)
    return {"id": str(b.id), "name": b.name, "limit_amount": str(b.limit_amount),
            "spent_amount": str(b.spent_amount)}


@router.post("/monetization/budgets/{budget_id}/spend")
def spend_budget(budget_id: uuid.UUID, payload: dict, session: Session = Depends(db)):
    try:
        b = MonetizationService.spend_budget(session, _tenant_id(payload), budget_id,
                                             Decimal(str(payload.get("amount", "0"))))
    except KeyError:
        raise HTTPException(404, "budget not found")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": str(b.id), "spent_amount": str(b.spent_amount),
            "limit_amount": str(b.limit_amount)}


@router.post("/finance/cost-centers", status_code=201)
def create_cost_center(payload: dict, session: Session = Depends(db)):
    c = MonetizationService.create_cost_center(session, _tenant_id(payload), payload)
    return {"id": str(c.id), "code": c.code, "name": c.name, "budget": str(c.budget)}


@router.post("/finance/profit-centers", status_code=201)
def create_profit_center(payload: dict, session: Session = Depends(db)):
    p = MonetizationService.create_profit_center(session, _tenant_id(payload), payload)
    return {"id": str(p.id), "code": p.code, "name": p.name, "target": str(p.target)}


@router.post("/analytics/feature-adoption", status_code=201)
def record_feature_adoption(payload: dict, session: Session = Depends(db)):
    a = MonetizationService.record_feature_adoption(
        session, _tenant_id(payload), payload.get("feature"), int(payload.get("subscriber_count", 0)),
        int(payload.get("usage_count", 0)), payload.get("period", "MONTH"))
    return {"id": str(a.id), "feature": a.feature, "subscriber_count": a.subscriber_count,
            "usage_count": a.usage_count, "period": a.period}


@router.post("/analytics/partner-sla", status_code=201)
def partner_sla(payload: dict, session: Session = Depends(db)):
    m = MonetizationService.partner_sla_analytics(
        session, _tenant_id(payload), payload.get("partner"), float(payload.get("sla_pct", 100)),
        int(payload.get("breaches", 0)), payload.get("period", "MONTH"))
    return {"id": str(m.id), "partner": m.partner, "sla_pct": m.sla_pct, "breaches": m.breaches}


@router.post("/analytics/churn/track", status_code=201)
def track_churn(payload: dict, session: Session = Depends(db)):
    c = MonetizationService.track_churn(session, _tenant_id(payload),
                                        payload.get("subscriber_id"), payload.get("stage", "AT_RISK"),
                                        payload.get("reason"))
    return {"id": str(c.id), "subscriber_id": c.subscriber_id, "stage": c.stage,
            "churned_at": c.churned_at}


@router.post("/analytics/trials", status_code=201)
def start_trial(payload: dict, session: Session = Depends(db)):
    t = MonetizationService.start_trial(session, _tenant_id(payload),
                                        payload.get("subscriber_id"), payload.get("plan"))
    return {"id": str(t.id), "subscriber_id": t.subscriber_id, "plan": t.plan, "converted": False}


@router.post("/analytics/trials/{trial_id}/convert")
def convert_trial(trial_id: uuid.UUID, payload: dict, session: Session = Depends(db)):
    try:
        t = MonetizationService.convert_trial(session, _tenant_id(payload), trial_id)
    except KeyError:
        raise HTTPException(404, "trial not found")
    return {"id": str(t.id), "converted": t.converted, "converted_at": t.converted_at}


@router.get("/analytics/trials/conversion-rate")
def trial_conversion_rate(tenant_id: uuid.UUID, session: Session = Depends(db)):
    return MonetizationService.trial_conversion_rate(session, tenant_id)


@router.post("/analytics/stickiness", status_code=201)
def compute_stickiness(payload: dict, session: Session = Depends(db)):
    s = MonetizationService.compute_stickiness(
        session, _tenant_id(payload), payload.get("product"),
        float(payload.get("retention_pct", 0)), payload.get("period", "MONTH"))
    return {"id": str(s.id), "product": s.product, "retention_pct": s.retention_pct,
            "stickiness_score": s.stickiness_score}
