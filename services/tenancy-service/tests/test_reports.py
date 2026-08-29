"""Tenant-aware reporting + authorized platform aggregates + exports."""
from datetime import datetime, timezone

import pytest

from app.domain.exceptions import ScopeExpansionError
from app.models import AggregateProjection
from app.services import commission_service, report_service


def test_tenant_report(session, tenant, make_partner, make_commission_plan):
    partner = make_partner()
    plan, rule = make_commission_plan()
    commission_service.create_agreement(session, tenant.id, partner_id=partner.id, plan_id=plan.id)
    commission_service.recognize_earning(session, tenant.id, partner_id=partner.id,
                                         source_event_id="evt-r1", source_event_type="billing.payment.captured.v1",
                                         basis="PAYMENT_COLLECTION", basis_amount=2000)
    session.commit()
    snapshot = report_service.generate_tenant_report(session, tenant.id, report_type="overview",
                                                     scope_kind="TENANT", generated_by="admin")
    session.commit()
    assert snapshot.metrics["commission"] == pytest.approx(200.0)
    franchise = report_service.generate_tenant_report(session, tenant.id, report_type="overview",
                                                      scope_kind="FRANCHISE", scope_id=partner.id,
                                                      generated_by="admin")
    assert franchise.scope_kind == "FRANCHISE"


def test_unsupported_scope_rejected(session, tenant):
    with pytest.raises(ScopeExpansionError):
        report_service.generate_tenant_report(session, tenant.id, report_type="overview",
                                              scope_kind="PLATFORM_AGGREGATE", generated_by="admin")


def test_platform_aggregate_with_freshness(session, tenant, tenant_b):
    report_service.upsert_aggregate(session, metric="commission", dimension="tenant", period_key="2026-08",
                                    value=100.0, source_tenant_id=tenant.id)
    report_service.upsert_aggregate(session, metric="commission", dimension="tenant", period_key="2026-08",
                                    value=50.0, source_tenant_id=tenant_b.id)
    session.commit()
    agg = report_service.platform_aggregate(session, metric="commission", period_key="2026-08",
                                            requested_by="platform")
    assert agg["total"] == pytest.approx(150.0)
    assert agg["tenant_count"] == 2
    assert agg["freshness_at"] is not None
    rows = list(session.scalars(__import__("sqlalchemy").select(AggregateProjection).where(
        AggregateProjection.metric == "commission")))
    assert len(rows) == 2


def test_export_job(session, tenant):
    job = report_service.request_export(session, tenant.id, export_type="customers", scope_kind="TENANT",
                                        requested_by="admin")
    assert job.state == "QUEUED"
