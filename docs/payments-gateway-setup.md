# Milestone 4 — Gateway Setup Guide

## Supported gateways

- **Razorpay** (first production adapter, `revenue/gateways/razorpay.py`).
- **FakePaymentGateway** (`revenue/gateways/fake.py`) — deterministic, used for
  automated tests and simulations only; never enabled in production.
- Adapter interface: `revenue/gateways/base.py` (`PaymentGateway` ABC with
  capability discovery). Future gateways (Cashfree, PayU, PhonePe, Stripe, ...)
  implement the same interface.

## Gateway-neutral contract

`create_payment / retrieve_payment / capture_payment / cancel_payment /
create_refund / retrieve_refund / verify_webhook / parse_webhook /
fetch_transactions / fetch_settlements / health_check`.

## Razorpay configuration (per tenant)

1. Create a Razorpay account; generate an **API key + secret** (Test mode for
   sandbox, Live mode for production).
2. Configure a **webhook** in the Razorpay dashboard → Settings → Webhooks:
   - URL: `https://<gateway>/api/bss/webhooks/gateway/<gateway_account_id>?tenant_id=<tenant_id>`
   - Events: `payment.captured`, `payment.failed`, `refund.processed`
   - Copy the generated **webhook secret**.
3. Register the gateway account via the API (credentials are **encrypted at
   rest** and never returned):
   `POST /api/bss/gateway-accounts` with `api_key`, `secret`, `webhook_secret`,
   `mode`, `currency`, `methods`.
4. Set the default account / priority for payment-method routing.

## Webhook security

- Raw body is preserved; the signature (`X-Razorpay-Signature`, HMAC-SHA256 over
  the raw body) is **verified before parsing**.
- Gateway event IDs are deduplicated (unique `(tenant, external_event_id)`); a
  duplicated webhook never posts twice.
- Payloads are redacted (no PAN/CVV/UPI PIN/tokens stored).

## Go-live checklist

- `BSS_GATEWAY_LIVE=true` only for sandbox/integration tests.
- Rotate webhook/API secrets via credential rotation (re-encrypt at rest).
- Never place live and test gateway accounts without the `mode` field.
- Card data never transits the platform (hosted checkout + tokenization).
