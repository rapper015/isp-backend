"""Organization hierarchy, partners, agreements, ownership and transfers."""
import uuid

import pytest

from app.domain.exceptions import CircularHierarchyError, DuplicateError, ValidationError
from app.services import organization_service


def test_create_org_unit_hierarchy(session, tenant):
    franchise = organization_service.create_org_unit(session, tenant.id, unit_type="FRANCHISE",
                                                     code="FR-1", name="Franchise 1")
    branch = organization_service.create_org_unit(session, tenant.id, unit_type="BRANCH",
                                                  code="BR-1", name="Branch 1", parent_id=franchise.id)
    team = organization_service.create_org_unit(session, tenant.id, unit_type="TEAM",
                                                code="TM-1", name="Support Team", parent_id=branch.id)
    assert branch.path.startswith(franchise.path + "/")
    assert team.path.startswith(branch.path + "/")


def test_circular_reparent_denied(session, tenant):
    a = organization_service.create_org_unit(session, tenant.id, unit_type="BRANCH", code="A", name="A")
    b = organization_service.create_org_unit(session, tenant.id, unit_type="BRANCH", code="B", name="B",
                                             parent_id=a.id)
    with pytest.raises(CircularHierarchyError):
        organization_service.reparent_org_unit(session, tenant.id, a.id, new_parent_id=b.id)


def test_cross_tenant_parent_denied(session, tenant, tenant_b):
    from app.domain.exceptions import NotFoundError as NFE

    a = organization_service.create_org_unit(session, tenant.id, unit_type="BRANCH", code="A", name="A")
    with pytest.raises(NFE):
        organization_service.create_org_unit(session, tenant_b.id, unit_type="BRANCH",
                                             code="B", name="B", parent_id=a.id)


def test_partner_lifecycle(session, tenant, make_partner):
    partner = make_partner(code="FRANCHISE-1")
    assert partner.status == "ACTIVE"
    partner = organization_service.change_partner_status(session, tenant.id, partner.id,
                                                         to_status="SUSPENDED", reason="breach")
    assert partner.status == "SUSPENDED"
    with pytest.raises(ValidationError):
        organization_service.change_partner_status(session, tenant.id, partner.id,
                                                   to_status="PROSPECT", reason="x")


def test_partner_link_and_cycle(session, tenant, make_partner):
    parent = make_partner(code="DIST-1", partner_type="DISTRIBUTOR")
    child = make_partner(code="FR-9", partner_type="FRANCHISE")
    row = organization_service.link_partners(session, tenant.id, parent.id, child.id)
    assert row.relationship_type == "FRANCHISE_OF"


def test_agreement_and_service_scope(session, tenant, make_partner):
    partner = make_partner()
    agreement = organization_service.create_agreement(session, tenant.id, partner_id=partner.id,
                                                      code="AG-1", customer_ownership_model="PARTNER_OWNED")
    version = organization_service.add_agreement_version(session, tenant.id, agreement.id,
                                                         terms={"commission_rate": 0.1})
    assert version.version == 1
    scope = organization_service.add_service_scope(session, tenant.id, partner.id,
                                                   service="SUPPORT", enabled=True)
    assert scope.enabled is True


def test_ownership_and_transfer(session, tenant, make_partner):
    partner = make_partner()
    ownership = organization_service.set_ownership(session, tenant.id, customer_id="CUST-1",
                                                   owning_org_unit_id=partner.org_unit_id,
                                                   acquisition_partner_id=partner.id)
    assert ownership.customer_id == "CUST-1"
    transfer = organization_service.transfer_customer(session, tenant.id, customer_id="CUST-1",
                                                      to_owner_id=None, reason="to tenant", requested_by="admin")
    assert transfer.state == "REQUESTED"
    transfer = organization_service.approve_transfer(session, tenant.id, transfer.id, approved_by="reviewer")
    assert transfer.state == "COMPLETED"


def test_data_access_grant(session, tenant):
    unit_a = organization_service.create_org_unit(session, tenant.id, unit_type="BRANCH", code="GA", name="A")
    unit_b = organization_service.create_org_unit(session, tenant.id, unit_type="BRANCH", code="GB", name="B")
    grant = organization_service.create_grant(session, tenant.id, granting_org_unit_id=unit_a.id,
                                              receiving_org_unit_id=unit_b.id, resource_type="customer",
                                              resource_scope={"ids": ["C-1"]}, permission="customers.view",
                                              purpose="billing support", approved_by="platform")
    assert grant.resource_type == "customer"


def test_duplicate_partner_code(session, tenant):
    organization_service.create_partner(session, tenant.id, partner_type="FRANCHISE", code="DUP", name="A")
    session.commit()
    with pytest.raises(DuplicateError):
        organization_service.create_partner(session, tenant.id, partner_type="FRANCHISE",
                                            code="DUP", name="B")
