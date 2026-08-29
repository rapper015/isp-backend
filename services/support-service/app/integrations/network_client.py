"""Network-control adapter (real): policy context + reapplication. Network
control lives in aaa-service; the support service only requests approved
operations through it."""
from __future__ import annotations

from .base import ActionResult, register
from .http import _HttpClient


@register
class NetworkClient(_HttpClient):
    name = "network"

    def __init__(self):
        super().__init__("SUPPORT_NETWORK_BASE_URL", "AAA_INTERNAL_API_KEY")

    def get_policy_context(self, subscriber_username: str | None, subscription_id: str | None):
        from .base import ok_result

        data = self.get_json(f"/api/aaa/network-control/context?username={subscriber_username or ''}&subscription_id={subscription_id or ''}")
        return ok_result(**data)

    def reapply_policy(self, *, subscriber_username: str, ticket_id: str, actor: str, correlation_id: str) -> ActionResult:
        data = self.post_json("/api/aaa/network-control/reapply", {"username": subscriber_username, "ticket_id": ticket_id, "actor": actor}, correlation_id=correlation_id)
        return ActionResult(ok=True, reference=data.get("job_id"), detail=data)
