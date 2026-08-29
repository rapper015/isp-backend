"""AAA adapter (real): session/auth context, CoA and disconnect via aaa-service.
The support service NEVER talks to a router or FreeRADIUS directly."""
from __future__ import annotations

from .base import ActionResult, register
from .http import _HttpClient


@register
class AAAClient(_HttpClient):
    name = "aaa"

    def __init__(self):
        super().__init__("SUPPORT_AAA_BASE_URL", "AAA_INTERNAL_API_KEY")

    def get_session_context(self, subscriber_username: str | None, calling_station_id: str | None):
        from .base import ok_result

        data = self.get_json(f"/api/aaa/sessions/context?username={subscriber_username or ''}&calling_station_id={calling_station_id or ''}")
        return ok_result(**data)

    def disconnect_and_reauth(self, *, subscriber_username: str, ticket_id: str, actor: str, correlation_id: str) -> ActionResult:
        data = self.post_json("/api/aaa/control/disconnect-reauth", {"username": subscriber_username, "ticket_id": ticket_id, "actor": actor}, correlation_id=correlation_id)
        return ActionResult(ok=True, reference=data.get("job_id"), detail=data)

    def request_coa(self, *, subscriber_username: str, attributes: dict | None, ticket_id: str, actor: str, correlation_id: str) -> ActionResult:
        data = self.post_json("/api/aaa/control/coa", {"username": subscriber_username, "attributes": attributes or {}, "ticket_id": ticket_id, "actor": actor}, correlation_id=correlation_id)
        return ActionResult(ok=True, reference=data.get("coa_id"), detail=data)

    def request_reconciliation(self, *, ticket_id: str, actor: str, correlation_id: str) -> ActionResult:
        data = self.post_json("/api/aaa/reconciliation/request", {"ticket_id": ticket_id, "actor": actor}, correlation_id=correlation_id)
        return ActionResult(ok=True, reference=data.get("job_id"), detail=data)

    def nas_reachability(self, nas_reference: str, *, ticket_id: str, actor: str, correlation_id: str) -> ActionResult:
        data = self.post_json(f"/api/aaa/nas/{nas_reference}/health-check", {"ticket_id": ticket_id, "actor": actor}, correlation_id=correlation_id)
        return ActionResult(ok=data.get("reachable", False), reference=nas_reference, detail=data)
