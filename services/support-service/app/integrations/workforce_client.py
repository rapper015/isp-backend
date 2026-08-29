"""Workforce adapter (real): field job creation."""
from __future__ import annotations

from .base import ActionResult, register
from .http import _HttpClient


@register
class WorkforceClient(_HttpClient):
    name = "workforce"

    def __init__(self):
        super().__init__("SUPPORT_WORKFORCE_BASE_URL", "WORKFORCE_INTERNAL_API_KEY")

    def create_job(self, *, tenant_id: str, job_type: str, ticket_id: str, service_location_id: str | None,
                   requested_at: str | None, required_skill: str | None, notes: str | None, actor: str, correlation_id: str) -> ActionResult:
        data = self.post_json("/api/workforce/jobs", {
            "tenant_id": tenant_id, "job_type": job_type, "ticket_id": ticket_id,
            "service_location_id": service_location_id, "requested_at": requested_at,
            "required_skill": required_skill, "notes": notes, "actor": actor,
        }, correlation_id=correlation_id)
        return ActionResult(ok=True, reference=data.get("job_number"), detail=data)
