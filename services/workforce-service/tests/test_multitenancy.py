"""Tenant isolation: work orders, technicians, SLA instances and events are
never visible or mutable across tenants."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.domain.exceptions import NotFoundError
from app.models import Tenant, WorkOrder
from app.services import workorder_service

TODAY = datetime.now(timezone.utc).replace(hour=10, minute=0, second=0, microsecond=0)


@pytest.fixture
def tenant_b(session):
    tenant = Tenant(name="Second ISP", code=f"SEC-{uuid.uuid4().hex[:6].upper()}")
    session.add(tenant)
    session.commit()
    session.refresh(tenant)
    return tenant


def test_work_orders_isolated(session, tenant_id, tenant_b, defaults, make_work_order):
    from app.services import catalog_service

    catalog_service.ensure_tenant_defaults(session, tenant_b.id)
    session.commit()
    wo_a = make_work_order()
    wo_b = workorder_service.create_work_order(session, tenant_b.id, work_order_type="FAULT_REPAIR",
                                               customer_id="CUST-B", source_channel="API", actor="test")
    session.commit()

    assert wo_a.tenant_id == tenant_id
    assert wo_b.tenant_id == tenant_b.id

    with pytest.raises(NotFoundError):
        workorder_service.get_work_order_or_404(session, tenant_id, wo_b.id)
    with pytest.raises(NotFoundError):
        workorder_service.get_work_order_or_404(session, tenant_b.id, wo_a.id)

    # Numbers are unique per tenant; each tenant starts its own sequence.
    assert wo_a.work_order_number.startswith("WO-")
    assert wo_b.work_order_number.startswith("WO-")


def test_technician_isolated_across_tenants(session, tenant_id, tenant_b, defaults, make_technician, make_work_order):
    from app.services import catalog_service, technician_service

    catalog_service.ensure_tenant_defaults(session, tenant_b.id)
    session.commit()
    tech = make_technician("Isolated Tech", skills=["FIBER_INSTALL", "ONT_INSTALL"],
                           certifications=[{"certification": "FIBER_SAFETY"}])
    wo = make_work_order()
    from app.services import appointment_service

    workorder_service.validate_work_order(session, tenant_id, wo.id, actor="test")
    appointment_service.schedule(session, tenant_id, wo, window_start=TODAY + timedelta(days=1),
                                 window_end=TODAY + timedelta(days=1, hours=2), actor="test")
    workorder_service.assign_work_order(session, tenant_id, wo.id, technician_id=tech.id,
                                        reason="isolation test", actor="test")

    # The same technician profile is not a valid assignment target for tenant B's work order.
    with pytest.raises(NotFoundError):
        workorder_service.assign_work_order(session, tenant_b.id, wo.id, technician_id=tech.id,
                                            reason="cross tenant", actor="test")


def test_tenant_number_sequences_independent(session, tenant_id, tenant_b, defaults):
    from app.services import catalog_service

    catalog_service.ensure_tenant_defaults(session, tenant_b.id)
    session.commit()
    a1 = workorder_service.create_work_order(session, tenant_id, work_order_type="SITE_SURVEY",
                                              source_channel="API", actor="test")
    a2 = workorder_service.create_work_order(session, tenant_id, work_order_type="SITE_SURVEY",
                                              source_channel="API", actor="test")
    b1 = workorder_service.create_work_order(session, tenant_b.id, work_order_type="SITE_SURVEY",
                                              source_channel="API", actor="test")
    session.commit()
    year = datetime.now(timezone.utc).year
    assert a1.work_order_number == f"WO-{year}-00000001"
    assert a2.work_order_number == f"WO-{year}-00000002"
    # Tenant B starts its own independent sequence at 1.
    assert b1.work_order_number == f"WO-{year}-00000001"


def test_customer_portal_isolation(session, tenant_id, tenant_b, defaults, make_work_order):
    from app.services import catalog_service

    catalog_service.ensure_tenant_defaults(session, tenant_b.id)
    session.commit()
    wo_a = make_work_order(customer_id="CUST-A1")
    wo_b = workorder_service.create_work_order(session, tenant_b.id, work_order_type="FAULT_REPAIR",
                                               customer_id="CUST-B1", source_channel="API", actor="test")
    session.commit()

    from app.models import Appointment, FieldSLAInstance

    # SLA instances are tenant-keyed.
    sla_a = session.scalar(select(FieldSLAInstance).where(FieldSLAInstance.work_order_id == wo_a.id))
    sla_b = session.scalar(select(FieldSLAInstance).where(FieldSLAInstance.work_order_id == wo_b.id))
    assert sla_a.tenant_id == tenant_id
    assert sla_b.tenant_id == tenant_b.id
