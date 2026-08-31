"""Tenant-aware worker tasks: outbox flush, aggregate projection refresh,
expired impersonation cleanup, credential expiry, stale tenant DB health.
Every task explicitly scopes its tenant; tasks never inherit ambient context."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .context import TenantContext, reset_context, set_context
from .events import envelope, unprocessed_events
from .models import ApiCredential, ImpersonationSession, Tenant, TenantDatabase


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _publish(event) -> None:
    """Declared aio-pika hook (no-op placeholder); outbox remains source of truth."""
    _ = envelope(event)


def flush_outbox(session: Session, limit: int = 100) -> int:
    events = unprocessed_events(session, limit)
    published = 0
    for event in events:
        try:
            _publish(event)
            event.published_at = _now()
            published += 1
        except Exception:  # noqa: BLE001
            event.attempts += 1
    session.commit()
    return published


def refresh_aggregates(session: Session, tenant_id) -> int:
    """Refresh the tenant's authorized aggregate projections (privacy-preserving)."""
    from sqlalchemy import func

    from .models import CommissionEarning
    from .services import report_service

    total = session.scalar(select(func.coalesce(func.sum(CommissionEarning.amount), 0)).where(
        CommissionEarning.tenant_id == tenant_id, CommissionEarning.status != "CLAWED_BACK")) or 0
    period = _now().strftime("%Y-%m")
    report_service.upsert_aggregate(session, metric="commission", dimension="tenant", period_key=period,
                                    value=float(total), source_tenant_id=tenant_id)
    session.commit()
    return 1


def expire_impersonations(session: Session, tenant_id) -> int:
    expired = list(session.scalars(select(ImpersonationSession).where(
        ImpersonationSession.tenant_id == tenant_id, ImpersonationSession.state == "ACTIVE",
        ImpersonationSession.expires_at.is_not(None), ImpersonationSession.expires_at < _now())))
    for row in expired:
        row.state = "EXPIRED"
        row.ended_at = _now()
    session.commit()
    return len(expired)


def expire_credentials(session: Session, tenant_id) -> int:
    expired = list(session.scalars(select(ApiCredential).where(
        ApiCredential.tenant_id == tenant_id, ApiCredential.status == "ACTIVE",
        ApiCredential.expires_at.is_not(None), ApiCredential.expires_at < _now())))
    for row in expired:
        row.status = "EXPIRED"
    session.commit()
    return len(expired)


def check_tenant_databases(session: Session, tenant_id) -> list[str]:
    """Health-check the tenant's database records (control plane only)."""
    rows = list(session.scalars(select(TenantDatabase).where(TenantDatabase.tenant_id == tenant_id)))
    results = []
    for row in rows:
        healthy = row.state == "READY"
        row.health_state = "HEALTHY" if healthy else "UNKNOWN"
        results.append(f"{row.alias}:{'OK' if healthy else 'FAIL'}")
    session.commit()
    return results


def run_tenant_tasks(session: Session, tenant_id) -> dict:
    """Run all tenant-scoped maintenance for one tenant (explicit scope)."""
    token = set_context(TenantContext(tenant_id=tenant_id, db_alias="control", auth_method="worker"))
    try:
        impersonations = expire_impersonations(session, tenant_id)
        credentials = expire_credentials(session, tenant_id)
        databases = check_tenant_databases(session, tenant_id)
        aggregates = refresh_aggregates(session, tenant_id)
        return {"tenant_id": str(tenant_id), "impersonations_expired": impersonations,
                "credentials_expired": credentials, "databases": databases,
                "aggregates_refreshed": aggregates}
    finally:
        reset_context(token)
