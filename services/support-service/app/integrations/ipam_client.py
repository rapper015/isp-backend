"""IPAM adapter (real): IP assignment reconciliation via ipam-service."""
from __future__ import annotations

from .base import ActionResult, register
from .http import _HttpClient


@register
class IPAMClient(_HttpClient):
    name = "ipam"

    def __init__(self):
        super().__init__("SUPPORT_IPAM_BASE_URL", "IPAM_INTERNAL_API_KEY")

    def reconcile_assignment(self, *, subscription_id: str | None, expected_ip: str | None, ticket_id: str, actor: str, correlation_id: str) -> ActionResult:
        data = self.post_json("/api/ipam/reconciliation/request", {
            "subscription_id": subscription_id, "expected_ip": expected_ip, "ticket_id": ticket_id, "actor": actor,
        }, correlation_id=correlation_id)
        return ActionResult(ok=True, reference=data.get("request_id"), detail=data)
