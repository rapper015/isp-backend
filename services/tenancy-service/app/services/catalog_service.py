"""Seeds the global permission registry, role templates, default system roles,
separation-of-duty constraints, feature flags, quotas and ledger accounts.
Idempotent — safe to call on every startup."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.access import DEFAULT_ROLE_TEMPLATES, DEFAULT_SOD
from ..models import (
    Entitlement,
    FeatureFlag,
    Permission,
    Quota,
    Role,
    RolePermission,
    RoleTemplate,
    SodConstraint,
)

_DEFAULT_FEATURES = [
    ("portal.white_label", "White-label customer portal", True),
    ("portal.custom_domain", "Custom domain hosting", True),
    ("franchise.customer_transfer", "Cross-franchise customer transfer", True),
    ("franchise.self_signup", "Franchise self signup", False),
    ("finance.partial_settlement", "Partial settlement payouts", True),
    ("security.require_mfa_partner", "Require MFA for partner admins", False),
]

_DEFAULT_QUOTAS = [
    ("USERS", None), ("CUSTOMERS", None), ("SUBSCRIBERS", None),
    ("NAS_DEVICES", None), ("CPE_DEVICES", None), ("STORAGE_BYTES", None),
    ("API_REQUESTS", None), ("REPORTS", None), ("EXPORTS", None),
    ("RABBITMQ_THROUGHPUT", None), ("BACKGROUND_JOBS", None),
    ("NOTIFICATION_VOLUME", None), ("FIRMWARE_ROLLOUT_SIZE", None),
]

_DEFAULT_ENTITLEMENTS = [
    ("franchise", "Franchise & partner management"),
    ("commission", "Commission engine"),
    ("settlement", "Partner settlement"),
    ("aggregate_report", "Platform aggregate reporting"),
]

_DEFAULT_ACCOUNTS = [
    ("partner_payable", "Partner settlements payable", "LIABILITY"),
    ("partner_earnings", "Partner commission earnings", "LIABILITY"),
    ("wallet_cash", "Partner wallet cash", "ASSET"),
    ("commission_expense", "Commission expense", "EXPENSE"),
    ("withholding_tax", "Tax withheld on settlements", "LIABILITY"),
]


def ensure_defaults(session: Session) -> None:
    for code in ("customers.view", "customers.create", "customers.own.view", "tenants.manage",
                 "tenants.create", "tenants.view", "tenants.activate", "tenants.suspend",
                 "tenants.offboard", "tenants.export", "domains.manage", "config.manage",
                 "feature.manage", "entitlements.manage", "quota.manage", "org.units.manage",
                 "partners.manage", "partners.create", "partners.view", "agreements.manage",
                 "agreements.approve", "ownership.manage", "ownership.transfer", "grants.manage",
                 "memberships.manage", "roles.manage", "permissions.manage", "access.review",
                 "service_accounts.manage", "impersonate", "commissions.manage",
                 "commissions.calculate", "commissions.plan.approve", "settlements.manage",
                 "settlements.calculate", "settlements.approve", "settlements.reversal",
                 "payouts.record", "payouts.reconcile", "wallet.adjust", "reports.view",
                 "reports.export", "reports.aggregate", "audit.view", "billing.invoice.issue",
                 "payments.refund.request", "payments.refund.approve", "payments.record",
                 "firmware.upload", "firmware.approve", "tickets.view", "workforce.dispatch",
                 "orders.cancel", "sessions.disconnect", "routers.configure",
                 "firmware.rollout.approve", "tickets.assign"):
        if session.scalars(select(Permission).where(Permission.code == code)).first() is None:
            session.add(Permission(code=code, description=code))

    for code, template in DEFAULT_ROLE_TEMPLATES.items():
        if session.scalars(select(RoleTemplate).where(RoleTemplate.code == code)).first() is None:
            session.add(RoleTemplate(code=code, name=code.replace("_", " ").title(),
                                     permission_codes=template["permissions"]))
        if session.scalars(select(Role).where(Role.tenant_id.is_(None), Role.code == code)).first() is None:
            role = Role(tenant_id=None, code=code, name=code.replace("_", " ").title(), is_system=True)
            session.add(role)
            session.flush()
            for perm in template["permissions"]:
                session.add(RolePermission(role_id=role.id, permission_code=perm))

    for operation, maker, checker in DEFAULT_SOD:
        if session.scalars(select(SodConstraint).where(SodConstraint.operation == operation)).first() is None:
            session.add(SodConstraint(operation=operation, maker_permission=maker,
                                      checker_permission=checker))

    for code, name, default in _DEFAULT_FEATURES:
        if session.scalars(select(FeatureFlag).where(FeatureFlag.code == code)).first() is None:
            session.add(FeatureFlag(code=code, name=name, platform_default=default))

    for code, _name in _DEFAULT_ENTITLEMENTS:
        if session.scalars(select(Entitlement).where(Entitlement.code == code)).first() is None:
            session.add(Entitlement(code=code, name=code))

    for kind, default_limit in _DEFAULT_QUOTAS:
        if session.scalars(select(Quota).where(Quota.kind == kind)).first() is None:
            session.add(Quota(kind=kind, default_limit=default_limit))

    from ..domain.ledger import ensure_account

    for code, name, kind in _DEFAULT_ACCOUNTS:
        ensure_account(session, None, code=code, name=name, kind=kind)
    session.flush()
