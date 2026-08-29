"""Scoped RBAC: memberships, roles, separation of duty, approvals, service
accounts, credentials, impersonation."""
import uuid

import pytest

from app.domain.exceptions import PermissionDeniedError, SeparationOfDutyError, ValidationError
from app.domain.access import check_permission, check_sod
from app.services import access_service


def test_permission_check_deny_by_default():
    with pytest.raises(PermissionDeniedError):
        check_permission("customers.view", {"reports.view"})
    check_permission("customers.view", {"customers.view"})
    check_permission("customers.view", {"*"})


def test_separation_of_duty_enforced(session, tenant):
    from app.models import SodConstraint

    constraints = list(session.scalars(
        __import__("sqlalchemy").select(SodConstraint).where(SodConstraint.operation == "settlement.approve")))
    with pytest.raises(SeparationOfDutyError):
        check_sod("alice", "alice", operation="settlement.approve", constraints=constraints)
    # Different actors are fine.
    check_sod("alice", "bob", operation="settlement.approve", constraints=constraints)


def test_membership_and_role(session, tenant):
    membership = access_service.create_membership(session, tenant.id, user_id="user-1", granted_by="admin")
    role = access_service.create_role(session, tenant.id, code="SUPPORT_AGENT", name="Support")
    access_service.set_role_permissions(session, tenant.id, role.id,
                                        permission_codes=["tickets.view", "customers.view"])
    assignment = access_service.assign_role(session, tenant.id, membership_id=membership.id,
                                            role_id=role.id, scope_kind="BRANCH", assigned_by="admin")
    assert assignment.scope_kind == "BRANCH"
    permissions = access_service.effective_permissions(session, tenant.id, "user-1")
    assert "tickets.view" in permissions


def test_approval_maker_checker(session, tenant):
    approval = access_service.request_approval(session, tenant.id, operation="tenant.activate",
                                               requested_by="creator")
    # Creator cannot approve their own request (SoD).
    from app.domain.exceptions import SeparationOfDutyError as SodErr

    with pytest.raises(SodErr):
        access_service.decide_approval(session, tenant.id, approval.id, decision="APPROVED",
                                       decided_by="creator")
    approved = access_service.decide_approval(session, tenant.id, approval.id, decision="APPROVED",
                                              decided_by="reviewer")
    assert approved.state == "APPROVED"


def test_service_account_and_credential(session, tenant):
    account = access_service.create_service_account(session, tenant.id, service="billing", name="billing-sa",
                                                    permission_codes=["reports.view"])
    credential = access_service.issue_api_credential(session, tenant.id, service_account_id=account.id,
                                                     name="key-1", expires_in_days=30)
    from app.domain.secrets import decrypt_secret

    assert decrypt_secret(credential.secret_ref)  # secret round-trips
    rotated = access_service.rotate_api_credential(session, tenant.id, credential.id)
    assert rotated.status == "ACTIVE"
    access_service.revoke_api_credential(session, tenant.id, credential.id)
    assert rotated.status == "REVOKED"


def test_impersonation_requires_reason_and_expires(session, tenant):
    from app.domain.exceptions import ImpersonationError

    with pytest.raises(ImpersonationError):
        access_service.start_impersonation(session, tenant.id, admin_user="admin", target_user="user",
                                           reason="")
    row = access_service.start_impersonation(session, tenant.id, admin_user="admin", target_user="user",
                                             reason="ticket #42", ticket_ref="#42", ttl_minutes=10)
    assert row.state == "ACTIVE" and row.read_only is True
    assert row.expires_at is not None
    access_service.end_impersonation(session, tenant.id, row.id)
    assert row.state == "EXPIRED"


def test_membership_revocation(session, tenant):
    membership = access_service.create_membership(session, tenant.id, user_id="user-9")
    access_service.revoke_membership(session, tenant.id, membership.id)
    assert membership.status == "REVOKED"
