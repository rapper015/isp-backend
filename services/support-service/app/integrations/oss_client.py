"""OSS adapter (real): subscriber context + service orders."""
from __future__ import annotations

from .base import ActionResult, register
from .http import _HttpClient


@register
class OSSClient(_HttpClient):
    name = "oss"

    def __init__(self):
        super().__init__("SUPPORT_OSS_BASE_URL", "OSS_INTERNAL_API_KEY")

    def get_subscriber_context(self, subscription_id: str | None, subscriber_username: str | None):
        from .base import ok_result

        data = self.get_json(f"/api/oss/subscribers/context?subscription_id={subscription_id or ''}&username={subscriber_username or ''}")
        return ok_result(**data)

    def create_order(self, *, tenant_id: str, order_type: str, customer_id: str | None, subscription_id: str | None,
                     service_location_id: str | None, requested_snapshot: dict | None, actor: str, correlation_id: str) -> ActionResult:
        data = self.post_json("/api/oss/orders", {
            "tenant_id": tenant_id, "order_type": order_type, "customer_id": customer_id,
            "service_subscription_id": subscription_id, "service_location_id": service_location_id,
            "requested_snapshot": requested_snapshot or {}, "actor": actor,
        }, correlation_id=correlation_id)
        return ActionResult(ok=True, reference=data.get("order_number"), detail=data)

    def retry_order_step(self, order_reference: str, step: str | None, *, actor: str, correlation_id: str) -> ActionResult:
        data = self.post_json(f"/api/oss/orders/{order_reference}/retry", {"step": step, "actor": actor}, correlation_id=correlation_id)
        return ActionResult(ok=True, reference=order_reference, detail=data)
