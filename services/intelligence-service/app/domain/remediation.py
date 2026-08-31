"""Remediation safety: autonomy levels, policy evaluation, kill switch,
budgets, cooldowns, circuit breakers, idempotency and blast radius.

Safety first: models never mutate domain state directly. Any operational
action must materialize as a RemediationIntent that passes policy evaluation
and (for L2+) approval before the authoritative service executes it.
"""
from __future__ import annotations

from .exceptions import (ApprovalError, CrossTenantActionError, KillSwitchEngagedError,
                         RemediationSafetyError)

# Autonomy level -> max allowed blast radius and whether approval is required.
AUTONOMY_POLICY = {
    "L0": {"approval_required": False, "executable": False},  # insight only
    "L1": {"approval_required": False, "executable": False},  # recommendation only
    "L2": {"approval_required": True, "executable": True},    # intent + approval
    "L3": {"approval_required": False, "executable": True},   # pre-approved, low-impact, reversible
    "L4": {"approval_required": True, "executable": False},   # high-impact autonomy, prohibited by default
}

# Action categories that must NEVER default above L2.
HIGH_IMPACT_CATEGORIES = ("CUSTOMER", "FINANCE", "SECURITY", "NETWORK_ACCESS", "DEVICE_CONFIG", "IPAM")


def autonomy_for_action(action_type: str, default_level: str = "L2") -> str:
    """Never allow high-impact categories above L2 by default."""
    for category in HIGH_IMPACT_CATEGORIES:
        if category.lower() in action_type.lower():
            return "L2"
    return default_level


def requires_approval(level: str) -> bool:
    return AUTONOMY_POLICY.get(level, AUTONOMY_POLICY["L2"])["approval_required"]


def is_executable(level: str) -> bool:
    return AUTONOMY_POLICY.get(level, AUTONOMY_POLICY["L2"])["executable"]


def check_kill_switch(global_switch: bool, tenant_switch: bool) -> None:
    if global_switch or tenant_switch:
        raise KillSwitchEngagedError("remediation disabled by kill switch")


def check_tenant_scope(tenant_id, policy_tenant_scope: str, action_tenant_id) -> None:
    if policy_tenant_scope == "TENANT" and str(tenant_id) != str(action_tenant_id):
        raise CrossTenantActionError("cross-tenant remediation action blocked")


def check_budget(used: int, policy_budget: int) -> None:
    if used >= policy_budget:
        raise RemediationSafetyError("remediation action budget exhausted")


def check_cooldown(last_executed_ts, cooldown_seconds, now_ts) -> None:
    if last_executed_ts is not None and (now_ts - last_executed_ts) < cooldown_seconds:
        raise RemediationSafetyError("remediation in cooldown period")


def check_rate_limit(executions_last_hour: int, limit_per_hour: int) -> None:
    if executions_last_hour >= limit_per_hour:
        raise RemediationSafetyError("remediation rate limit exceeded")


def check_circuit_breaker(recent_failures: int, threshold: int) -> None:
    if recent_failures >= threshold:
        raise RemediationSafetyError("remediation circuit breaker open")


def check_preconditions(record: dict, preconditions: list) -> None:
    from .fraud import eval_condition
    for condition in preconditions:
        if not eval_condition(record, condition):
            raise RemediationSafetyError(f"precondition not met: {condition}")


def check_blast_radius(count: int, max_radius: int) -> None:
    if count > max_radius:
        raise RemediationSafetyError("remediation exceeds max blast radius")


def require_approval_for_level(level: str) -> None:
    """L2+ intents require a recorded human approval before execution."""
    if requires_approval(level):
        return
    raise ApprovalError(f"approval not required for level {level}")


def verify_approval(approvals: list, level: str, tenant_id) -> None:
    """At least one APPROVED approval must exist for executable L2+ intents."""
    if not requires_approval(level):
        return
    if not any(a.get("decision") == "APPROVED" for a in approvals):
        raise ApprovalError("remediation intent requires approval before execution")
