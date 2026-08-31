"""NMS adapter (real): device/outage context."""
from __future__ import annotations

from .base import ActionResult, register
from .http import _HttpClient


@register
class NMSClient(_HttpClient):
    name = "nms"

    def __init__(self):
        super().__init__("SUPPORT_NMS_BASE_URL", "NMS_INTERNAL_API_KEY")

    def get_device_context(self, nas_reference: str | None, pop: str | None, service_location_id: str | None):
        from .base import ok_result

        data = self.get_json(f"/api/nms/context?nas={nas_reference or ''}&pop={pop or ''}&location={service_location_id or ''}")
        return ok_result(**data)

    def list_active_outages(self, tenant_id: str | None):
        from .base import ok_result

        data = self.get_json(f"/api/nms/outages?status=ACTIVE&tenant_id={tenant_id or ''}")
        return ok_result(outages=data.get("outages", []))

    def create_noc_investigation(self, *, ticket_id: str, actor: str, correlation_id: str) -> ActionResult:
        data = self.post_json("/api/nms/investigations", {"ticket_id": ticket_id, "actor": actor}, correlation_id=correlation_id)
        return ActionResult(ok=True, reference=data.get("investigation_id"), detail=data)
