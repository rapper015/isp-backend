"""Notifications adapter (real): delivery requests to the notification service.
The support service requests delivery; the notification service owns transport,
retries and provider results."""
from __future__ import annotations

from .base import ActionResult, register
from .http import _HttpClient


@register
class NotificationsClient(_HttpClient):
    name = "notifications"

    def __init__(self):
        super().__init__("SUPPORT_NOTIFICATIONS_BASE_URL", "NOTIFICATIONS_INTERNAL_API_KEY")

    def send(self, *, channel: str, recipient: str, template: str, variables: dict, ticket_id: str | None, correlation_id: str) -> ActionResult:
        data = self.post_json("/api/notifications/send", {
            "channel": channel, "recipient": recipient, "template": template,
            "variables": variables, "ticket_id": str(ticket_id) if ticket_id else None,
        }, correlation_id=correlation_id)
        return ActionResult(ok=True, reference=data.get("delivery_reference"), detail=data)
