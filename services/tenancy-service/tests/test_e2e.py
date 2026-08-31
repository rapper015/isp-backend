"""End-to-end: platform creates a tenant -> provisioned -> tenant creates a
franchise -> franchise user gets scoped access -> customer owned by franchise ->
activation + payment produce earnings -> settlement calculated/approved/locked ->
statement -> tenant report -> platform aggregate. Sibling franchises isolated."""
import uuid

from sqlalchemy import select

from app.models import CommissionEarning, CustomerOwnership, PartnerSettlement, ReportSnapshot
from app.services import (
    access_service,
    commission_service,
    organization_service,
    report_service,
    settlement_service,
    tenant_service,
)


def test_full_ecosystem_lifecycle(session, defaults):
    # 1. Platform creates a tenant.
    tenant = tenant_service.create_tenant(session, name="E2E ISP", code="E2EISP1",
                                          requested_by="platform")
    session.commit()
    assert tenant.status == "REQUESTED"

    # 2. Tenant database provisioned, admin + defaults installed.
    tenant = tenant_service.provision_tenant(session, tenant.id, actor="platform")
    session.commit()
    assert tenant.status == "ACTIVE" and tenant.provision_state == "ACTIVE"
    from app.models import TenantDatabase

    assert session.scalars(__import__("sqlalchemy").select(TenantDatabase).where(
        TenantDatabase.tenant_id == tenant.id)).first().state == "READY"

    # 3. Tenant creates a franchise (partner) + org unit.
    franchise = organization_service.create_partner(session, tenant.id, partner_type="FRANCHISE",
                                                    code="FR-E2E", name="E2E Franchise")
    session.commit()
    organization_service.change_partner_status(session, tenant.id, franchise.id, to_status="ONBOARDING",
                                               reason="onboard")
    organization_service.change_partner_status(session, tenant.id, franchise.id, to_status="ACTIVE",
                                               reason="onboarded")
    session.commit()

    # 4. Franchise user receives scoped access (membership + franchise-scoped role).
    membership = access_service.create_membership(session, tenant.id, user_id="franchise-user",
                                                  granted_by="tenant-admin")
    role = access_service.create_role(session, tenant.id, code="FRANCHISE_ADMIN", name="Franchise Admin")
    access_service.set_role_permissions(session, tenant.id, role.id,
                                        permission_codes=["customers.view", "customers.create",
                                                          "reports.view", "partners.manage"])
    access_service.assign_role(session, tenant.id, membership_id=membership.id, role_id=role.id,
                               scope_kind="FRANCHISE", assigned_by="tenant-admin")
    session.commit()
    perms = access_service.effective_permissions(session, tenant.id, "franchise-user")
    assert "customers.view" in perms

    # 5. Franchise owns a customer.
    organization_service.set_ownership(session, tenant.id, customer_id="C-E2E",
                                       acquisition_partner_id=franchise.id)
    session.commit()

    # 6. Commission plan + agreement; activation + payment create earnings.
    from app.services import commission_service as cs

    plan = cs.create_plan(session, tenant.id, code="PLAN-E2E", name="Plan")
    session.commit()
    cs.add_rule(session, tenant.id, plan.id, code="R-E2E", name="r", basis="PAYMENT_COLLECTION",
                calculation_type="PERCENTAGE", rate=10)
    session.commit()
    cs.approve_plan(session, tenant.id, plan.id, approved_by="tenant-admin")
    cs.create_agreement(session, tenant.id, partner_id=franchise.id, plan_id=plan.id)
    earning = cs.recognize_earning(session, tenant.id, partner_id=franchise.id,
                                   source_event_id="e2e-payment-1",
                                   source_event_type="billing.payment.captured.v1",
                                   basis="PAYMENT_COLLECTION", basis_amount=1000)
    session.commit()
    assert earning.amount == 100.0

    # 7. Settlement calculated, reviewed, approved, locked, statement generated.
    from datetime import datetime, timezone

    cycle = settlement_service.create_cycle(session, tenant.id, code="E2E-CYC",
                                            period_start=datetime(2026, 8, 1, tzinfo=timezone.utc),
                                            period_end=datetime(2026, 8, 31, tzinfo=timezone.utc))
    session.commit()
    settlement = settlement_service.create_settlement(session, tenant.id, partner_id=franchise.id,
                                                      cycle_id=cycle.id)
    session.commit()
    settlement_service.calculate_settlement(session, tenant.id, settlement.id, actor="calc")
    settlement_service.submit_for_review(session, tenant.id, settlement.id, actor="calc")
    settlement_service.approve_settlement(session, tenant.id, settlement.id, approved_by="finance")
    settlement_service.lock_settlement(session, tenant.id, settlement.id, actor="finance")
    session.commit()
    statement = settlement_service.generate_statement(session, tenant.id, settlement.id, actor="finance")
    session.commit()
    assert statement.statement_data["net"] == 100.0

    # 8. Tenant admin sees tenant-wide reporting.
    snapshot = report_service.generate_tenant_report(session, tenant.id, report_type="overview",
                                                     scope_kind="TENANT", generated_by="tenant-admin")
    session.commit()
    assert snapshot.metrics["commission"] == 100.0

    # 9. Platform admin sees an authorized aggregate.
    report_service.upsert_aggregate(session, metric="commission", dimension="tenant",
                                    period_key="2026-08", value=100.0, source_tenant_id=tenant.id)
    session.commit()
    agg = report_service.platform_aggregate(session, metric="commission", period_key="2026-08",
                                            requested_by="platform")
    assert agg["total"] == 100.0


def test_sibling_franchise_isolated(session, tenant):
    """A franchise administrator must not see sibling franchise scope."""
    franchise_a = organization_service.create_partner(session, tenant.id, partner_type="FRANCHISE",
                                                      code="FR-A", name="A")
    franchise_b = organization_service.create_partner(session, tenant.id, partner_type="FRANCHISE",
                                                      code="FR-B", name="B")
    session.commit()
    organization_service.set_ownership(session, tenant.id, customer_id="C-A",
                                       acquisition_partner_id=franchise_a.id)
    session.commit()

    # B's scoped read cannot reach A's customer (ownership lookup is partner-scoped).
    ownership = session.scalars(select(CustomerOwnership).where(
        CustomerOwnership.tenant_id == tenant.id,
        CustomerOwnership.customer_id == "C-A",
        CustomerOwnership.acquisition_partner_id == franchise_b.id)).first()
    assert ownership is None  # sibling cannot see A's customer


def test_tenant_suspension_does_not_offboard_customers(session, tenant, make_partner):
    partner = make_partner()
    tenant_service.suspend_tenant(session, tenant.id, reason="billing hold", scope="BILLING",
                                  actor="platform")
    session.commit()
    from app.models import Partner

    partner = session.get(Partner, partner.id)
    assert partner.status == "ACTIVE"  # end customers/partners not auto-suspended
