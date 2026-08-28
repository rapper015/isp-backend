"""NAS integration adapter — reuses the existing AAA/MikroTik RouterOS
integration from milestone-0 through the AAA service; OSS never talks to
RouterOS directly."""
from __future__ import annotations

from .base import Adapter, ok_result, register


@register
class NasClient(Adapter):
    name = "nas"

    def configure_subscriber(self, tenant_id, nas_reference, aaa_subscriber_reference, username, plan_reference) -> dict:
        return ok_result(
            {
                "nas_reference": nas_reference,
                "aaa_subscriber_reference": aaa_subscriber_reference,
                "configured": True,
                "via": "aaa-service/routeros-integration",
            }
        )

    def remove_subscriber(self, tenant_id, nas_reference, aaa_subscriber_reference) -> dict:
        return ok_result({"nas_reference": nas_reference, "removed": True})
