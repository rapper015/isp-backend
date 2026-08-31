"""Deterministic fake gateway for tests/simulations only.

Never used in production paths. It signs webhooks with the account's webhook
secret so contract tests can verify signature processing end-to-end."""
from __future__ import annotations

import itertools
from decimal import Decimal
from typing import Any

from .base import GatewayOrder, GatewayResult, PaymentGateway, register, sign_payload

_COUNTER = itertools.count(1)


@register
class FakePaymentGateway(PaymentGateway):
    code = "FAKE"

    def __init__(self):
        self.orders: dict[str, dict] = {}
        self.captures: dict[str, dict] = {}
        self.fail_capture = False
        self.fail_create = False
        self.capture_status = "captured"

    def create_payment(self, *, amount, currency, description, idempotency_key, return_url=None, account=None) -> GatewayOrder:
        if self.fail_create:
            raise RuntimeError("gateway create failed")
        reference = f"fake-order-{next(_COUNTER)}"
        self.orders[reference] = {"amount": str(amount), "currency": currency, "status": "created"}
        return GatewayOrder(gateway_order_ref=reference, safe_payload={"gateway": "fake", "order_id": reference, "checkout_url": f"https://pay.example/checkout/{reference}"})

    def retrieve_payment(self, gateway_ref: str) -> GatewayResult:
        order = self.orders.get(gateway_ref, {})
        return GatewayResult(status=order.get("status", "failed"), external_ref=gateway_ref, detail={"amount": order.get("amount")})

    def capture_payment(self, gateway_ref: str, amount: Decimal) -> GatewayResult:
        if self.fail_capture:
            return GatewayResult(status="failed", external_ref=gateway_ref, detail={"error": "injected failure"})
        self.captures[gateway_ref] = {"amount": str(amount), "status": self.capture_status}
        self.orders[gateway_ref] = {**self.orders.get(gateway_ref, {}), "status": self.capture_status}
        return GatewayResult(status=self.capture_status, external_ref=gateway_ref, detail={"amount": str(amount)})

    def cancel_payment(self, gateway_ref: str) -> GatewayResult:
        if gateway_ref in self.orders:
            self.orders[gateway_ref]["status"] = "cancelled"
        return GatewayResult(status="cancelled", external_ref=gateway_ref, detail={})

    def create_refund(self, gateway_ref: str, amount: Decimal, reference: str) -> GatewayResult:
        return GatewayResult(status="refunded", external_ref=gateway_ref, detail={"refund_id": f"fake-refund-{reference[-8:]}", "amount": str(amount)})

    def retrieve_refund(self, gateway_ref: str, refund_ref: str) -> GatewayResult:
        return GatewayResult(status="refunded", external_ref=gateway_ref, detail={"refund_id": refund_ref})

    def verify_webhook(self, raw_body: str, signature: str, secret: str) -> bool:
        return sign_payload(secret, raw_body) == signature

    def parse_webhook(self, raw_body: str) -> dict:
        import json

        return json.loads(raw_body)

    def fetch_transactions(self) -> list[dict]:
        return [{"external_ref": ref, "amount": data["amount"], "status": data["status"]} for ref, data in self.captures.items()]

    def fetch_settlements(self) -> list[dict]:
        return []

    def health_check(self) -> bool:
        return not self.fail_capture
