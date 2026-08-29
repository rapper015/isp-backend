"""Identity & onboarding: discovery, tenant resolution, secure claiming,
duplicates, cross-tenant protection, transfer, decommission and assignment."""
import uuid

import pytest

from app.domain.exceptions import (
    AmbiguousOwnershipError,
    DeviceClaimError,
    TenantIsolationError,
    ValidationError,
)
from app.models import CpeOnboarding, CpeOwnershipHistory, ManagedCpe
from app.services import device_service


def test_discover_with_tenant_identifies_device(session, tenant_id, acs, make_acs_device):
    acs_device_id = make_acs_device(serial_number="SN-A1", oui="A4B1C1", product_class="AN5506")
    device = device_service.discover_from_acs(session, acs["instance"].id, acs_device_id=acs_device_id,
                                              requested_tenant_id=tenant_id, actor="test")
    assert device.state == "IDENTIFIED"
    assert device.tenant_id == tenant_id
    assert device.serial_number == "SN-A1"
    assert device.oui == "A4B1C1"


def test_discover_unknown_device_quarantined(session, acs, make_acs_device):
    acs_device_id = make_acs_device(serial_number="SN-UNKNOWN", oui="AA0001", product_class="MYSTERY")
    device = device_service.discover_from_acs(session, acs["instance"].id, acs_device_id=acs_device_id,
                                              actor="test")
    assert device.state == "QUARANTINED"
    assert device.tenant_id is None


def test_claim_with_preregistered_serial(session, tenant_id, acs, make_acs_device):
    from app.integrations.fakes import STATE

    asset_id = STATE.seed_inventory_asset("SN-C1", model="ONT")
    acs_device_id = make_acs_device(serial_number="SN-C1", oui="A4B1C1", product_class="AN5506")
    device = device_service.discover_from_acs(session, acs["instance"].id, acs_device_id=acs_device_id,
                                              requested_tenant_id=tenant_id, actor="test")
    outcome = device_service.resolve_tenant(session, device, method="PREREGISTERED_SERIAL",
                                            claimed_tenant_id=tenant_id, actor="test")
    assert outcome == "MATCHED"
    device = device_service.claim_device(session, tenant_id, device.id, method="PREREGISTERED_SERIAL",
                                         evidence=asset_id, actor="test")
    assert device.state == "CLAIMED"
    assert device.claimed_by == "test"
    history = session.query(CpeOwnershipHistory).filter_by(cpe_id=device.id).first()
    assert history is not None and history.transfer_type == "CLAIM"


def test_ambiguous_claim_quarantines(session, tenant_id, acs, make_acs_device):
    acs_device_id = make_acs_device(serial_number="SN-AMB", oui="A4B1C1", product_class="AN5506")
    device = device_service.discover_from_acs(session, acs["instance"].id, acs_device_id=acs_device_id,
                                              requested_tenant_id=tenant_id, actor="test")
    outcome = device_service.resolve_tenant(session, device, method="CIRCUIT_SERVICE_MAPPING",
                                            claimed_tenant_id=tenant_id, actor="test")
    assert outcome == "UNKNOWN"
    with pytest.raises(AmbiguousOwnershipError):
        device_service.claim_device(session, tenant_id, device.id, method="CIRCUIT_SERVICE_MAPPING", actor="test")
    session.refresh(device)
    assert device.state in ("QUARANTINED", "CLAIM_PENDING")


def test_cross_tenant_claim_blocked(session, tenant_id, tenant_b, acs, make_acs_device):
    acs_device_id = make_acs_device(serial_number="SN-X1", oui="A4B1C1", product_class="AN5506")
    device = device_service.discover_from_acs(session, acs["instance"].id, acs_device_id=acs_device_id,
                                              requested_tenant_id=tenant_id, actor="test")
    device_service.claim_device(session, tenant_id, device.id, method="ADMIN_CLAIM", actor="test")
    session.commit()
    with pytest.raises(TenantIsolationError):
        device_service.claim_device(session, tenant_b, device.id, method="ADMIN_CLAIM", actor="test")


def test_transfer_requires_reason_and_preserves_history(session, tenant_id, tenant_b, acs, make_acs_device):
    device, _ = None, None
    acs_device_id = make_acs_device(serial_number="SN-T1", oui="A4B1C1", product_class="AN5506")
    device = device_service.discover_from_acs(session, acs["instance"].id, acs_device_id=acs_device_id,
                                              requested_tenant_id=tenant_id, actor="test")
    device_service.claim_device(session, tenant_id, device.id, method="ADMIN_CLAIM", actor="test")
    session.commit()
    with pytest.raises(ValidationError):
        device_service.transfer_device(session, tenant_id, tenant_b, device.id, reason="", actor="test")
    device = device_service.transfer_device(session, tenant_id, tenant_b, device.id, reason="franchise moved",
                                            actor="test")
    assert device.tenant_id == tenant_b
    history = session.query(CpeOwnershipHistory).filter_by(cpe_id=device.id).all()
    assert any(h.transfer_type == "TRANSFER" for h in history)


def test_decommission(session, tenant_id, acs, make_acs_device):
    acs_device_id = make_acs_device(serial_number="SN-D1", oui="A4B1C1", product_class="AN5506")
    device = device_service.discover_from_acs(session, acs["instance"].id, acs_device_id=acs_device_id,
                                              requested_tenant_id=tenant_id, actor="test")
    device_service.claim_device(session, tenant_id, device.id, method="ADMIN_CLAIM", actor="test")
    device = device_service.decommission_device(session, tenant_id, device.id, reason="end of life", actor="test")
    assert device.state == "DECOMMISSIONED"
    from app.domain.exceptions import DuplicateError

    with pytest.raises(DuplicateError):
        device_service.decommission_device(session, tenant_id, device.id, reason="again", actor="test")


def test_assign_links_business_entities(session, tenant_id, acs, make_acs_device):
    acs_device_id = make_acs_device(serial_number="SN-A2", oui="A4B1C1", product_class="AN5506")
    device = device_service.discover_from_acs(session, acs["instance"].id, acs_device_id=acs_device_id,
                                              requested_tenant_id=tenant_id, actor="test")
    device_service.claim_device(session, tenant_id, device.id, method="ADMIN_CLAIM", actor="test")
    device = device_service.assign_device(session, tenant_id, device.id, customer_id="CUST-1",
                                          service_subscription_id="SUB-1", service_location_id="LOC-1",
                                          oss_order_id="ORD-1", work_order_id="WO-1",
                                          inventory_serial="SN-A2", inventory_asset_id="inv-A2", actor="test")
    assert device.customer_id == "CUST-1"
    assert device.service_subscription_id == "SUB-1"
    assert device.state == "ASSIGNED"


def test_duplicate_serial_detected(session, tenant_id, acs, make_acs_device):
    make_acs_device(serial_number="SN-DUP", oui="A4B1C1", product_class="AN5506", device_id="dev-1")
    device_service.discover_from_acs(session, acs["instance"].id, acs_device_id="dev-1",
                                     requested_tenant_id=tenant_id, actor="test")
    session.commit()
    make_acs_device(serial_number="SN-DUP", oui="A4B1C1", product_class="AN5506", device_id="dev-2")
    device2 = device_service.discover_from_acs(session, acs["instance"].id, acs_device_id="dev-2",
                                               requested_tenant_id=tenant_id, actor="test")
    # Same identity tuple -> returns the same managed CPE, never a duplicate.
    assert session.query(ManagedCpe).filter_by(serial_number="SN-DUP").count() == 1
    assert device2.serial_number == "SN-DUP"


def test_onboarding_records_resolution(session, tenant_id, acs, make_acs_device):
    acs_device_id = make_acs_device(serial_number="SN-R1", oui="A4B1C1", product_class="AN5506")
    device = device_service.discover_from_acs(session, acs["instance"].id, acs_device_id=acs_device_id,
                                              requested_tenant_id=tenant_id, actor="test")
    device_service.resolve_tenant(session, device, method="ADMIN_CLAIM", evidence="approved",
                                  claimed_tenant_id=tenant_id, actor="test")
    record = session.query(CpeOnboarding).filter_by(cpe_id=device.id).first()
    assert record is not None
    assert record.resolution_method == "ADMIN_CLAIM"
    assert record.result == "MATCHED"
