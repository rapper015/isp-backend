"""Explainable, rule-based CRM risk aggregation. Each change records the source
and reason. Manual overrides are allowed with expiry; no unexplained automated
termination decisions are made."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..enums import RISK_LEVELS, RISK_SOURCES
from ..models import Customer, CustomerRisk
from .audit_service import audit, correlation, outbox, timeline

_WEIGHTS = {"LOW": 25, "MEDIUM": 50, "HIGH": 75, "CRITICAL": 100}


def _aggregate_levels(levels: list[str]) -> str:
    if not levels:
        return "UNKNOWN"
    ranking = {level: i for i, level in enumerate(RISK_LEVELS)}
    return max(levels, key=lambda level: ranking.get(level, 0))


def record_risk(session: Session, tenant_id, customer_id, level: str, source: str, reason: str, source_event_id: str | None = None) -> CustomerRisk:
    """Record an explainable risk signal and recompute the customer's risk."""
    level = level.upper()
    if level not in RISK_LEVELS:
        raise ValueError(f"invalid risk level: {level}")
    source = source.upper()
    if source not in RISK_SOURCES and source != "MANUAL_REVIEW":
        raise ValueError(f"invalid risk source: {source}")
    risk = CustomerRisk(tenant_id=tenant_id, customer_id=customer_id, level=level, score=_WEIGHTS.get(level, 0), source=source, reason=reason, source_event_id=source_event_id, effective_level=level)
    session.add(risk)
    customer = session.get(Customer, customer_id)
    if customer is not None:
        # Effective level = latest override if not expired, else latest signal.
        latest = session.scalar(select(CustomerRisk).where(CustomerRisk.tenant_id == tenant_id, CustomerRisk.customer_id == customer_id).order_by(CustomerRisk.created_at.desc()).limit(1))
        effective = latest.override_level if latest and latest.override_level and not (latest.override_expires_at and latest.override_expires_at < datetime.now(timezone.utc)) else latest.level if latest else "UNKNOWN"
        customer.risk_level = effective
        risk.effective_level = effective
    request_id = correlation(None)
    audit(session, tenant_id, "system", "crm.customer.risk_changed", "customer", customer_id, safe_after={"level": level, "source": source}, reason=reason, correlation_id=request_id)
    outbox(session, "crm.customer.risk_changed.v1", tenant_id, request_id, {"customer_id": str(customer_id), "level": level, "source": source, "reason": reason})
    timeline(session, tenant_id, "RISK", f"Risk {level} from {source}", customer_id=customer_id, correlation_id=request_id)
    session.flush()
    return risk


def override_risk(session: Session, tenant_id, customer_id, level: str, reason: str, reviewer: str, expires_in_seconds: int = 0) -> CustomerRisk:
    """Authorized manual override. The override expires unless given a TTL."""
    level = level.upper()
    if level not in RISK_LEVELS:
        raise ValueError(f"invalid risk level: {level}")
    latest = session.scalar(select(CustomerRisk).where(CustomerRisk.tenant_id == tenant_id, CustomerRisk.customer_id == customer_id).order_by(CustomerRisk.created_at.desc()).limit(1))
    if latest is None:
        raise ValueError("no risk record to override")
    latest.override_level = level
    latest.override_reason = reason
    latest.reviewed_by = reviewer
    latest.reviewed_at = datetime.now(timezone.utc)
    latest.override_expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds) if expires_in_seconds > 0 else None
    customer = session.get(Customer, customer_id)
    if customer is not None:
        customer.risk_level = level
    request_id = correlation(None)
    audit(session, tenant_id, reviewer, "crm.customer.risk_override", "customer", customer_id, safe_after={"override_level": level}, reason=reason, correlation_id=request_id)
    session.flush()
    return latest


def risk_history(session: Session, tenant_id, customer_id) -> list[CustomerRisk]:
    return list(session.scalars(select(CustomerRisk).where(CustomerRisk.tenant_id == tenant_id, CustomerRisk.customer_id == customer_id).order_by(CustomerRisk.created_at.desc())))
