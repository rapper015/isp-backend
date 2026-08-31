"""CRM adapter (real): customer identity + contact context via gateway."""
from __future__ import annotations

from .base import register
from .http import _HttpClient


@register
class CRMClient(_HttpClient):
    name = "crm"

    def __init__(self):
        super().__init__("SUPPORT_CRM_BASE_URL", "CRM_INTERNAL_API_KEY")

    def get_customer_context(self, customer_id: str):
        from .base import ok_result

        data = self.get_json(f"/api/crm/customers/{customer_id}", correlation_id=None)
        return ok_result(
            customer_id=customer_id,
            customer_number=data.get("customer_number"),
            customer_name=data.get("name") or data.get("customer_name"),
            tier=data.get("tier"),
            lifecycle_state=data.get("lifecycle_state"),
            contact_preference=data.get("contact_preference"),
            service_location=data.get("primary_service_location_id"),
        )
