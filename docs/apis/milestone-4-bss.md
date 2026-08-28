# Milestone 4 — BSS Service: Billing & Payments API

Service: `bss-service`. Auth: `X-BSS-Service-Key` (internal) for `/api/bss/*`;
management JWT RBAC also supported. All `/api/bss/*` routes are tenant-scoped.

## Health / status

| Method | Path | Description |
| --- | --- | --- |
| GET | `/health` | Liveness probe |
| GET | `/status` | Service phase/status |

## Tenants & billing accounts

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/bss/tenants` | Create tenant (currency) |
| POST | `/api/bss/billing-accounts` | Create billing account |
| GET | `/api/bss/billing-accounts` | List billing accounts (credit/holds) |

## Invoices

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/bss/invoices` | Issue an invoice (derived status; publishes `invoice.issued.v1`) |
| GET | `/api/bss/invoices` | List invoices (account/status filters) |
| GET | `/api/bss/invoices/{invoice_id}` | Invoice detail + derived balance |
| GET | `/api/bss/billing-accounts/{billing_account_id}/outstanding` | Server-side payable + credit balance |

## Payment intents & checkout

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/bss/payment-intents` | Create PaymentIntent (server-side amount; idempotent) |
| POST | `/api/bss/payment-intents/{intent_id}/checkout` | Start hosted checkout (gateway order, safe payload) |
| POST | `/api/bss/payment-intents/{intent_id}/capture` | Authoritative idempotent capture |
| GET | `/api/bss/payment-intents` | List intents |

## Payments / allocations / receipts

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/bss/payments` | List payment transactions |
| GET | `/api/bss/payments/{transaction_id}/allocations` | Payment allocations (incl. reversals) |
| GET | `/api/bss/payments/{transaction_id}/receipt` | Download receipt (references immutable txn) |

## Gateway accounts

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/bss/gateway-accounts` | Register gateway account (credentials encrypted; never returned) |
| GET | `/api/bss/gateway-accounts` | List gateway accounts (no secrets) |

## Webhooks

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/bss/webhooks/gateway/{gateway_account_id}?tenant_id=...` | Receive gateway webhook (signature verified, deduped) |
| GET | `/api/bss/webhooks` | Webhook history (status/signature/redaction) |

## Refunds & chargebacks

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/bss/refunds` | Create refund (≤ refundable; partial/multiple) |
| POST | `/api/bss/refunds/{refund_id}/approve` | Approve + complete refund |
| GET | `/api/bss/refunds` | List refunds |
| POST | `/api/bss/chargebacks` | Record a chargeback/dispute (new immutable event) |

## Manual / offline payments (maker-checker)

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/bss/manual-payments` | Record a manual payment (DRAFT; approval if required) |
| POST | `/api/bss/manual-payments/{manual_id}/submit` | Submit for review |
| POST | `/api/bss/manual-payments/{manual_id}/approve` | Approve |
| POST | `/api/bss/manual-payments/{manual_id}/reject` | Reject |
| POST | `/api/bss/manual-payments/{manual_id}/post` | Post (immutable transaction + allocation + ledger) |
| POST | `/api/bss/manual-payments/{manual_id}/reverse` | Reverse a posted manual payment |

## Reconciliation

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/bss/reconciliation/batches` | Create batch + import gateway transactions |
| POST | `/api/bss/reconciliation/batches/{batch_id}/run` | Run deterministic matching |
| POST | `/api/bss/reconciliation/settlements` | Import gateway settlement (dedup) |
| GET | `/api/bss/reconciliation/exceptions` | Reconciliation exceptions |
| POST | `/api/bss/reconciliation/exceptions/{exception_id}/resolve` | Resolve an exception |

## Dunning

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/bss/dunning/policies` | Create dunning policy |
| POST | `/api/bss/dunning/stages` | Add a stage to a policy version |
| POST | `/api/bss/dunning/policies/{policy_id}/publish` | Publish policy (immutable) |
| POST | `/api/bss/dunning/cases` | Open a dunning case (account delinquent) |
| POST | `/api/bss/dunning/cases/{case_id}/advance` | Run next due stage (publishes suspension-required) |
| POST | `/api/bss/dunning/cases/{case_id}/pause` | Pause case |
| POST | `/api/bss/dunning/cases/{case_id}/resume` | Resume case |
| POST | `/api/bss/dunning/cases/{case_id}/resolve` | Resolve case |
| GET | `/api/bss/dunning/cases` | List dunning cases |
| POST | `/api/bss/dunning/promises` | Record promise-to-pay |
| POST | `/api/bss/dunning/holds` | Place collection/dispute/legal hold |

## Ledger

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/bss/ledger/entries` | Immutable journal entries |
| POST | `/api/bss/ledger/rebuild-projection` | Rebuild balance projection for a period |
| GET | `/api/bss/ledger/balances` | Ledger balance projections |

## Reports

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/bss/reports/daily-collections` | Daily collections |
| GET | `/api/bss/reports/invoice-aging` | Invoice aging buckets |
| GET | `/api/bss/reports/payment-methods` | Payment method summary |
| GET | `/api/bss/reports/refunds` | Refund summary |
| GET | `/api/bss/reports/chargebacks` | Chargeback summary |
| GET | `/api/bss/reports/settlements` | Settlement summary |
| GET | `/api/bss/reports/reconciliation-exceptions` | Reconciliation exception counts |
| GET | `/api/bss/reports/credit-balances` | Credit balance report |
| GET | `/api/bss/reports/outstanding` | Outstanding invoices |

## Legacy compatibility (Milestone 0-1 billing)

| Method | Path | Description |
| --- | --- | --- |
| POST | `/plans` | Create plan |
| GET | `/plans` | List plans |
| POST | `/invoices` | Legacy create invoice |
| GET | `/invoices` | Legacy list invoices |
| POST | `/payments` | Legacy record payment |
| GET | `/payments` | Legacy list payments |
