"""Tenant-aware reporting + authorized platform aggregates.

Every report requires an explicit authorized scope. Frontend filters can never
expand authorization scope (enforced by check_scope in the API layer)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..domain.exceptions import NotFoundError, ScopeExpansionError
from ..models import (
    AggregateProjection,
    CommissionEarning,
    ExportJob,
    Partner,
    PartnerSettlement,
    ReportSnapshot,
)
from .audit_service import audit, correlation
from .organization_service import get_org_unit_or_404, get_partner_or_404


def _now() -> datetime:
    return datetime.now(timezone.utc)


def generate_tenant_report(session: Session, tenant_id, *, report_type: str,
                           scope_kind: str, scope_id: uuid.UUID | None = None,
                           period_start=None, period_end=None,
                           generated_by: str = "system") -> ReportSnapshot:
    """Build a materialized report for the authorized scope."""
    if scope_kind not in ("TENANT", "FRANCHISE", "BRANCH", "ORG_UNIT"):
        raise ScopeExpansionError("unsupported report scope")
    if scope_kind == "FRANCHISE" and scope_id is not None:
        get_partner_or_404(session, tenant_id, scope_id)
    if scope_kind in ("BRANCH", "ORG_UNIT") and scope_id is not None:
        get_org_unit_or_404(session, tenant_id, scope_id)
    metrics = {}
    partner_filter = []
    if scope_kind == "FRANCHISE" and scope_id is not None:
        partner_filter = [Partner.id == scope_id]
    if report_type == "overview":
        earnings = session.scalar(select(func.coalesce(func.sum(CommissionEarning.amount), 0)).where(
            CommissionEarning.tenant_id == tenant_id,
            *([CommissionEarning.partner_id == scope_id] if scope_kind == "FRANCHISE" and scope_id else [])))
        settlements = session.scalar(select(func.count()).select_from(PartnerSettlement).where(
            PartnerSettlement.tenant_id == tenant_id,
            *([PartnerSettlement.partner_id == scope_id] if scope_kind == "FRANCHISE" and scope_id else [])))
        metrics = {"commission": float(earnings or 0), "settlements": int(settlements or 0)}
    snapshot = ReportSnapshot(tenant_id=tenant_id, scope_kind=scope_kind, scope_id=scope_id,
                              report_type=report_type, period_start=period_start, period_end=period_end,
                              metrics=metrics, generated_by=generated_by, generated_at=_now())
    session.add(snapshot)
    session.flush()
    audit(session, tenant_id, generated_by, "report.generated", resource_type="report",
          resource_id=snapshot.id, after={"report_type": report_type, "scope": scope_kind})
    return snapshot


def upsert_aggregate(session: Session, *, metric: str, dimension: str, period_key: str,
                     value: float, source_tenant_id: uuid.UUID) -> AggregateProjection:
    row = session.scalars(select(AggregateProjection).where(
        AggregateProjection.metric == metric, AggregateProjection.dimension == dimension,
        AggregateProjection.period_key == period_key,
        AggregateProjection.source_tenant_id == source_tenant_id)).first()
    if row is None:
        row = AggregateProjection(metric=metric, dimension=dimension, period_key=period_key,
                                  value=value, source_tenant_id=source_tenant_id, freshness_at=_now())
        session.add(row)
    else:
        row.value = value
        row.freshness_at = _now()
    session.flush()
    return row


def platform_aggregate(session: Session, *, metric: str, period_key: str | None = None,
                       dimension: str = "tenant", requested_by: str = "system") -> dict:
    """Authorized platform-wide aggregate — only privacy-preserving dimensions."""
    stmt = select(AggregateProjection).where(AggregateProjection.metric == metric,
                                             AggregateProjection.dimension == dimension)
    if period_key:
        stmt = stmt.where(AggregateProjection.period_key == period_key)
    rows = list(session.scalars(stmt))
    total = round(sum(r.value for r in rows), 2)
    audit(session, None, requested_by, "report.aggregate_access", resource_type="aggregate",
          after={"metric": metric, "dimension": dimension, "period": period_key},
          metadata={"source_tenant_ids": [str(r.source_tenant_id) for r in rows if r.source_tenant_id]})
    return {"metric": metric, "dimension": dimension, "period_key": period_key,
            "total": total, "tenant_count": len(rows),
            "freshness_at": max((r.freshness_at for r in rows), default=None),
            "rows": [{"tenant": str(r.source_tenant_id), "value": r.value} for r in rows]}


def request_export(session: Session, tenant_id, *, export_type: str, scope_kind: str,
                   scope_id: uuid.UUID | None = None, requested_by: str = "system") -> ExportJob:
    job = ExportJob(tenant_id=tenant_id, scope_kind=scope_kind, scope_id=scope_id,
                    export_type=export_type, state="QUEUED", requested_by=requested_by)
    session.add(job)
    session.flush()
    audit(session, tenant_id, requested_by, "report.export_requested", resource_type="export",
          resource_id=job.id, after={"export_type": export_type, "scope": scope_kind})
    return job
