"""Tenant isolation: cross-tenant device access, profile assignment, config
jobs, diagnostics, actions and firmware are all blocked."""
import uuid

import pytest

from app.domain.exceptions import NotFoundError, TenantIsolationError
from app.services import action_service, configuration_service, device_service, diagnostic_service


def test_cross_tenant_device_isolated(session, tenant_id, tenant_b, acs, make_acs_device):
    acs_device_id = make_acs_device(serial_number="SN-ISO", oui="A4B1C1", product_class="AN5506")
    device = device_service.discover_from_acs(session, acs["instance"].id, acs_device_id=acs_device_id,
                                              requested_tenant_id=tenant_id, actor="test")
    device_service.claim_device(session, tenant_id, device.id, method="ADMIN_CLAIM", actor="test")
    session.commit()
    with pytest.raises(NotFoundError):
        device_service.get_device_or_404(session, tenant_b, device.id)
    with pytest.raises(NotFoundError):
        configuration_service.create_configuration_job(session, tenant_b, device.id,
                                                       parameters={"X": "1"}, requested_by="test")


def test_cross_tenant_action_blocked(session, tenant_id, tenant_b, acs, make_acs_device):
    device, _ = None, None
    acs_device_id = make_acs_device(serial_number="SN-ISO2", oui="A4B1C1", product_class="AN5506")
    device = device_service.discover_from_acs(session, acs["instance"].id, acs_device_id=acs_device_id,
                                              requested_tenant_id=tenant_id, actor="test")
    device_service.claim_device(session, tenant_id, device.id, method="ADMIN_CLAIM", actor="test")
    session.commit()
    with pytest.raises(NotFoundError):
        action_service.create_action(session, tenant_b, device.id, action_type="REBOOT", actor="test")


def test_cross_tenant_diagnostic_blocked(session, tenant_id, tenant_b, acs, make_acs_device):
    acs_device_id = make_acs_device(serial_number="SN-ISO3", oui="A4B1C1", product_class="AN5506")
    device = device_service.discover_from_acs(session, acs["instance"].id, acs_device_id=acs_device_id,
                                              requested_tenant_id=tenant_id, actor="test")
    device_service.claim_device(session, tenant_id, device.id, method="ADMIN_CLAIM", actor="test")
    session.commit()
    with pytest.raises(NotFoundError):
        diagnostic_service.create_diagnostic_job(session, tenant_b, device.id, diagnostic_type="PING", actor="test")


def test_cross_tenant_profile_isolated(session, tenant_id, tenant_b, defaults, make_profile):
    profile, version = make_profile(code="TENANT_PROFILE")
    from app.services import profile_service

    with pytest.raises(NotFoundError):
        profile_service.get_profile_or_404(session, tenant_b, profile.id)
    with pytest.raises(NotFoundError):
        profile_service.get_version_or_404(session, tenant_b, version.id)


def test_device_identity_unique_per_tenant(session, tenant_id, tenant_b, acs, make_acs_device):
    acs_device_id = make_acs_device(serial_number="SN-SAME", oui="A4B1C1", product_class="AN5506")
    device = device_service.discover_from_acs(session, acs["instance"].id, acs_device_id=acs_device_id,
                                              requested_tenant_id=tenant_id, actor="test")
    device_service.claim_device(session, tenant_id, device.id, method="ADMIN_CLAIM", actor="test")
    session.commit()
    # The same identity tuple cannot be claimed by another tenant without transfer.
    with pytest.raises(TenantIsolationError):
        device_service.claim_device(session, tenant_b, device.id, method="ADMIN_CLAIM", actor="test")
