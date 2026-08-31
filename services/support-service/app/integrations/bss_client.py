"""BSS adapter (real): billing summary + billing/payment review actions."""
from __future__ import annotations

from .base import ActionResult, register
from .http import _HttpClient


@register
class BSSClient(_HttpClient):
    name = "bss"

    def __init__(self):
        super().__init__("SUPPORT_BSS_BASE_URL", "BSS_INTERNAL_API_KEY")

    def get_billing_context(self, billing_account_id: str | None, customer_id: str | None, include_payment_detail: bool = False):
        from .base import ok_result

        path = f"/api/bss/billing/accounts/{billing_account_id}" if billing_account_id else f"/api/bss/billing/summary?customer_id={customer_id}"
        data = self.get_json(path)
        return ok_result(
            billing_account_id=billing_account_id,
            customer_id=customer_id,
            billing_status=data.get("status"),
            outstanding_amount=data.get("outstanding_amount"),
            last_payment_at=data.get("last_payment_at"),
            financial_restriction=data.get("financial_restriction"),
            invoice_summary=data.get("invoice_summary"),
            currency=data.get("currency"),
        )

    def request_billing_review(self, ticket_id: str, *, actor: str, correlation_id: str) -> ActionResult:
        data = self.post_json("/api/bss/disputes/reviews", {"ticket_id": ticket_id, "actor": actor}, correlation_id=correlation_id)
        return ActionResult(ok=True, reference=data.get("review_number"), detail=data)

    def reconcile_payment(self, ticket_id: str, *, actor: str, correlation_id: str, amount: str | None = None) -> ActionResult:
        data = self.post_json("/api/bss/payments/reconciliation", {"ticket_id": ticket_id, "actor": actor, "amount": amount}, correlation_id=correlation_id)
        return ActionResult(ok=True, reference=data.get("batch_number"), detail=data)
