"""Scoped authorization + separation-of-duty rules.

Permissions are action-level codes (customers.view, billing.invoice.issue, ...).
A role resolves to a permission set. Separation-of-duty is evaluated from
SodConstraint rows: the maker of an operation may not be the checker for it."""
from __future__ import annotations

import uuid

from .exceptions import PermissionDeniedError, ScopeExpansionError, SeparationOfDutyError
from .identity import scope_expands

# Well-known platform default roles (seeded as RoleTemplate + system roles).
DEFAULT_ROLE_TEMPLATES = {
    "PLATFORM_ADMIN": {
        "permissions": ["*"],
        "scope": "PLATFORM_AGGREGATE",
    },
    "TENANT_ADMIN": {
        "permissions": ["tenants.manage", "memberships.manage", "roles.manage", "partners.manage",
                        "commissions.manage", "settlements.manage", "reports.view", "*financial.approve",
                        "governance.manage", "governance.view"],
        "scope": "TENANT",
    },
    "FRANCHISE_ADMIN": {
        "permissions": ["customers.view", "customers.create", "partners.manage", "commissions.manage",
                        "settlements.view", "reports.view", "workforce.dispatch"],
        "scope": "FRANCHISE",
    },
    "FRANCHISE_AGENT": {
        "permissions": ["customers.view", "customers.create", "customers.own.view", "tickets.view"],
        "scope": "ASSIGNED_CUSTOMERS",
    },
    "FINANCE_MANAGER": {
        "permissions": ["settlements.manage", "settlements.approve", "wallet.adjust", "commissions.manage",
                        "reports.view", "payouts.record"],
        "scope": "TENANT",
    },
    "AUDITOR": {
        "permissions": ["reports.view", "audit.view", "reports.export", "governance.view"],
        "scope": "TENANT",
    },
    "READ_ONLY": {
        "permissions": ["reports.view", "customers.view", "audit.view", "governance.view"],
        "scope": "BRANCH",
    },
}

# Default separation-of-duty constraints (operation -> maker permission -> checker permission).
DEFAULT_SOD = [
    ("payment.approve", "payments.record", "payments.refund.approve"),
    ("refund.approve", "payments.refund.request", "payments.refund.approve"),
    ("commission.calculate", "commissions.calculate", "settlements.approve"),
    ("firmware.approve", "firmware.upload", "firmware.approve"),
    ("settlement.approve", "settlements.calculate", "settlements.approve"),
    ("tenant.activate", "tenants.create", "tenants.activate"),
    ("payout.reconcile", "payouts.record", "payouts.reconcile"),
]


def permissions_for(role: str, role_permissions: dict | None = None) -> set[str]:
    """Resolve the permission set for a role name (used by the auth layer)."""
    role_permissions = role_permissions or {}
    return set(role_permissions.get(role, []))


def check_permission(permission: str, permissions: set[str]) -> None:
    if "*" in permissions or permission in permissions:
        return
    raise PermissionDeniedError(f"permission required: {permission}")


def check_scope(active_scope_kind: str, requested_scope_kind: str) -> None:
    if not scope_expands(active_scope_kind, requested_scope_kind):
        raise ScopeExpansionError("requested scope exceeds authorization scope")


def check_sod(maker: str, checker: str, *, operation: str, constraints: list) -> None:
    """Reject when the same actor is both maker and checker for a constrained op."""
    if maker == checker:
        for constraint in constraints:
            if constraint.operation == operation and constraint.is_active:
                raise SeparationOfDutyError(
                    f"separation of duty: {operation} cannot be approved by its requester")
    if maker == checker:
        raise SeparationOfDutyError(f"maker and checker cannot be the same actor for {operation}")


def validate_approval_transition(current: str, target: str) -> bool:
    transitions = {
        "PENDING": {"APPROVED", "REJECTED", "CANCELLED"},
        "APPROVED": set(),
        "REJECTED": set(),
        "CANCELLED": set(),
    }
    return target in transitions.get(current, set())
