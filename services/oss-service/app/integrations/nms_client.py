"""NMS integration adapter (service readiness verification)."""
from __future__ import annotations

from .base import Adapter, ok_result, register


@register
class NmsClient(Adapter):
    name = "nms"

    def verify_service_readiness(self, tenant_id, subscription_code, resources: dict) -> dict:
        required = {"ip_address", "port_reference"}
        missing = [key for key in required if key not in resources]
        if missing:
            return ok_result({"ready": False, "reason": f"missing resources: {', '.join(missing)}"})
        return ok_result({"ready": True, "probe": "ok", "subscription_code": subscription_code})

    def verify_link(self, tenant_id, subscription_code) -> dict:
        return ok_result({"ready": True, "probe": "link-up"})
