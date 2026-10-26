"""Pure helpers for the durable customer onboarding workflow."""
import hashlib
import json
from typing import Any


def request_fingerprint(payload: dict[str, Any]) -> str:
    """Return a stable fingerprint while ignoring the transport retry key."""
    canonical = json.loads(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str))
    canonical.get("order", {}).pop("idempotency_key", None)
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def onboarding_key(explicit_key: str | None, fingerprint: str) -> str:
    return explicit_key or f"customer-onboarding:{fingerprint}"


def final_response(local: dict, order: dict, activation: dict, activation_error: str | None = None) -> dict:
    return {
        **local,
        "order_id": order.get("id"),
        "order_number": order.get("order_number"),
        "order_state": activation.get("order_state", order.get("state")),
        "workflow_state": activation.get("workflow_state"),
        "subscription_id": activation.get("subscription_id"),
        "activation_error": activation_error,
    }
