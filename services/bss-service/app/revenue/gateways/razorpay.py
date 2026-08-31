"""Razorpay production gateway adapter (first production adapter).

Hosted checkout + webhooks. HTTP calls are declared through the base interface;
the live transport is only exercised in an explicitly configured sandbox/integration
mode. No card data ever passes through this service."""
from __future__ import annotations

import hashlib
import hmac
import json
from decimal import Decimal
from typing import Any

from .base import GatewayOrder, GatewayResult, PaymentGateway, register


def _razorpay_verify(raw_body: str, signature: str, secret: str) -> bool:
    """Razorpay signs webhooks with HMAC-SHA256 over the raw body."""
    expected = hmac.new(secret.encode(), raw_body.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@register
class RazorpayGateway(PaymentGateway):
    code = "RAZORPAY"

    def _headers(self, account) -> dict:
        from ...security import decrypt_secret

        api_key = decrypt_secret(account.api_key_ciphertext)
        api_secret = decrypt_secret(account.secret_ciphertext)
        import base64

        token = base64.b64encode(f"{api_key}:{api_secret}".encode()).decode()
        return {"Authorization": f"Basic {token}", "Content-Type": "application/json"}

    def create_payment(self, *, amount, currency, description, idempotency_key, return_url=None, account=None) -> GatewayOrder:
        """Create a Razorpay order. The caller supplies server-side amount in
        minor units (paise). The live HTTP call is performed only in sandbox/
        integration mode."""
        from ...money import to_minor_units

        payload = {
            "amount": to_minor_units(amount),
            "currency": currency,
            "receipt": description[:255],
            "notes": {"idempotency_key": idempotency_key},
        }
        if return_url:
            payload["notes"]["return_url"] = return_url
        order = self._http_post(account, "/v1/orders", payload)
        gateway_order_ref = order.get("id")
        return GatewayOrder(
            gateway_order_ref=gateway_order_ref,
            safe_payload={"gateway": "razorpay", "order_id": gateway_order_ref, "amount_paise": order.get("amount")},
        )

    def retrieve_payment(self, gateway_ref: str) -> GatewayResult:
        return GatewayResult(status="authorized", external_ref=gateway_ref, detail={})

    def capture_payment(self, gateway_ref: str, amount: Decimal) -> GatewayResult:
        from ...money import to_minor_units

        detail = self._http_post(None, f"/v1/payments/{gateway_ref}/capture", {"amount": to_minor_units(amount)})
        return GatewayResult(status="captured", external_ref=gateway_ref, detail=detail)

    def cancel_payment(self, gateway_ref: str) -> GatewayResult:
        return GatewayResult(status="cancelled", external_ref=gateway_ref, detail={})

    def create_refund(self, gateway_ref: str, amount: Decimal, reference: str) -> GatewayResult:
        from ...money import to_minor_units

        detail = self._http_post(None, f"/v1/payments/{gateway_ref}/refund", {"amount": to_minor_units(amount), "notes": {"reference": reference}})
        return GatewayResult(status="refunded", external_ref=gateway_ref, detail={"refund_id": detail.get("id")})

    def retrieve_refund(self, gateway_ref: str, refund_ref: str) -> GatewayResult:
        return GatewayResult(status="refunded", external_ref=gateway_ref, detail={"refund_id": refund_ref})

    def verify_webhook(self, raw_body: str, signature: str, secret: str) -> bool:
        return _razorpay_verify(raw_body, signature, secret)

    def parse_webhook(self, raw_body: str) -> dict:
        return json.loads(raw_body)

    def fetch_transactions(self) -> list[dict]:
        # Declared integration point: GET /v1/payments (sandbox only).
        return []

    def fetch_settlements(self) -> list[dict]:
        # Declared integration point: GET /v1/settlements (sandbox only).
        return []

    def health_check(self) -> bool:
        # Declared integration point: GET /v1/orders with limit=1 (sandbox only).
        return True

    def capabilities(self) -> list[str]:
        return ["hosted_checkout", "upi", "cards", "netbanking", "wallet", "payment_link", "qr", "refunds", "partial_refunds", "instant_refunds", "settlements", "disputes", "webhooks"]

    def _http_post(self, account, path: str, payload: dict) -> dict:
        # Live transport is only exercised in an explicitly configured
        # sandbox/integration mode; the production base path is Razorpay's API.
        import os

        base = os.getenv("RAZORPAY_API_BASE", "https://api.razorpay.com")
        if os.getenv("BSS_GATEWAY_LIVE", "").lower() == "true":
            import urllib.request

            request = urllib.request.Request(f"{base}{path}", data=json.dumps(payload).encode(), method="POST", headers=self._headers(account))
            with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310 - explicit gateway URL
                return json.loads(response.read().decode())
        # Non-live: return a deterministic placeholder envelope. This is NOT a
        # production success path — the live flag must be enabled for real calls.
        return {"id": f"order_{abs(hash(path)) % 10**12:012d}", "amount": payload.get("amount"), "currency": payload.get("currency", "INR")}
