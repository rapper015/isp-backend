"""Deterministic, secret-free desired-state planning for managed RouterOS AAA."""
from hashlib import sha256
import json

from .nas_desired_state import compute_plan, build_desired_assignments

def sanitize_configuration(configuration: dict) -> dict:
    """Reject secret-shaped fields before desired state or snapshots can persist."""
    prohibited = {"secret", "password", "token", "credential", "shared_secret"}
    def clean(value):
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items() if key.casefold() not in prohibited}
        if isinstance(value, list): return [clean(item) for item in value]
        return value
    return clean(configuration)

def configuration_hash(configuration: dict) -> str:
    return sha256(json.dumps(sanitize_configuration(configuration), sort_keys=True, separators=(",", ":")).encode()).hexdigest()

def build_plan(desired: dict, assignments: list, current: dict | None = None,
               managed_addresses: set[str] | None = None,
               capabilities: dict | None = None,
               tenant_policy: dict | None = None) -> tuple[list[dict], str, dict]:
    """Create a pure preview; it never contacts a router or exposes a secret.

    The returned tuple matches the previous contract: ``(changes, risk,
    validation)``. ``changes`` are stable ordered operations produced by the
    desired-state engine.
    """
    desired_assignments = build_desired_assignments(assignments)
    desired = dict(desired)
    desired["radius_assignments"] = desired_assignments
    plan = compute_plan(current, desired, desired_assignments, managed_addresses, capabilities, tenant_policy)
    return plan["operations"], plan["risk"], plan["validation"]
