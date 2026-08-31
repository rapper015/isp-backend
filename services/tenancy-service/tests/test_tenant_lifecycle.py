"""Tenant lifecycle: create/validate/provision saga, activation, suspend,
resume, restrict, offboard, archive, config, domains, features, quotas."""
import uuid

import pytest

from app.domain.exceptions import DuplicateError, TenantNotActiveError, ValidationError
from app.models import TenantDatabase, TenantDomain, TenantFeature, TenantQuota
from app.services import tenant_service


def test_create_tenant_requires_unique_code(session, tenant_id):
    tenant = tenant_service.create_tenant(session, name="A", code="DUP", requested_by="test")
    session.commit()
    with pytest.raises(DuplicateError):
        tenant_service.create_tenant(session, name="B", code="dup", requested_by="test")


def test_provision_saga_activates_tenant(session, tenant_id):
    tenant = tenant_service.get_tenant_or_404(session, tenant_id)
    tenant = tenant_service.provision_tenant(session, tenant.id, actor="platform")
    session.commit()
    assert tenant.status == "ACTIVE"
    assert tenant.provision_state == "ACTIVE"
    assert session.scalars(
        __import__("sqlalchemy").select(TenantDatabase).where(TenantDatabase.tenant_id == tenant.id)).first()
    # Re-running is idempotent.
    again = tenant_service.provision_tenant(session, tenant.id, actor="platform")
    assert again.status == "ACTIVE"


def test_suspend_requires_reason_and_records_scope(session, tenant):
    tenant_service.suspend_tenant(session, tenant.id, reason="invoice overdue", scope="BILLING",
                                  actor="platform")
    session.commit()
    suspended = tenant_service.get_tenant_or_404(session, tenant.id)
    assert suspended.status == "SUSPENDED"
    assert suspended.suspension_reason == "invoice overdue"


def test_suspend_requires_reason(session, tenant):
    with pytest.raises(ValidationError):
        tenant_service.suspend_tenant(session, tenant.id, reason="", actor="platform")


def test_resume_returns_to_active(session, tenant):
    tenant_service.suspend_tenant(session, tenant.id, reason="billing hold", actor="platform")
    session.commit()
    tenant = tenant_service.resume_tenant(session, tenant.id, actor="platform")
    assert tenant.status == "ACTIVE"


def test_offboard_and_archive(session, tenant):
    tenant = tenant_service.start_offboarding(session, tenant.id, reason="contract end", actor="platform")
    session.commit()
    assert tenant.status == "OFFBOARDING"
    tenant = tenant_service.archive_tenant(session, tenant.id, actor="platform")
    assert tenant.status == "ARCHIVED"


def test_restricted_tenant_still_functional(session, tenant):
    tenant = tenant_service.restrict_tenant(session, tenant.id, actor="platform")
    assert tenant.status == "RESTRICTED"


def test_versioned_config(session, tenant):
    row = tenant_service.set_config(session, tenant.id, "portal", {"branding": {"theme": {"primary": "#000"}}},
                                    actor="test")
    first_version = row.version
    row = tenant_service.set_config(session, tenant.id, "portal", {"branding": {"theme": {"primary": "#fff"}}},
                                    actor="test")
    assert row.version == first_version + 1
    config = tenant_service.get_config(session, tenant.id, "portal")
    assert config["branding"]["theme"]["primary"] == "#fff"


def test_domain_add_and_verify(session, tenant):
    row = tenant_service.add_domain(session, tenant.id, "shop.example.com", actor="test")
    assert row.is_verified is False
    row = tenant_service.verify_domain(session, tenant.id, row.id, token=row.verification_token, actor="test")
    assert row.is_verified is True and row.status == "ACTIVE"
    # Duplicate domain is rejected.
    with pytest.raises(DuplicateError):
        tenant_service.add_domain(session, tenant.id, "shop.example.com", actor="test")


def test_feature_override(session, tenant):
    assert tenant_service.get_feature(session, tenant.id, "portal.white_label") is True
    tenant_service.set_feature(session, tenant.id, "portal.white_label", False, actor="test")
    session.commit()
    assert tenant_service.get_feature(session, tenant.id, "portal.white_label") is False


def test_quota_enforcement(session, tenant):
    tenant_service.set_quota(session, tenant.id, "CPE_DEVICES", limit=2, actor="test")
    session.commit()
    assert tenant_service.check_quota(session, tenant.id, "CPE_DEVICES", requested=1) is True
    tenant_service.consume_quota(session, tenant.id, "CPE_DEVICES", amount=2, actor="test")
    session.commit()
    assert tenant_service.check_quota(session, tenant.id, "CPE_DEVICES", requested=1) is False


def test_tenant_health(session, tenant):
    health = tenant_service.tenant_health(session, tenant.id)
    assert health["status"] == "ACTIVE"
    assert any(c["type"] == "migrations" for c in health["checks"])
