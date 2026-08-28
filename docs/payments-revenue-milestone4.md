# Milestone 4 — Payments and Revenue Automation

## 1. Repository financial audit

The platform is the FastAPI microservice monorepo (not Django). Billing lives in
`services/bss-service` (FastAPI) with `legacy/` Django reference apps
(`billing`, `payments`, `plans`) that are **not** wired into the running service.

Current `bss-service` (minimal foundation):

| Component | State | Decision |
| --- | --- | --- |
| `Plan` model + `/plans` | Basic product | **KEEP/EXTEND** (currency, tenant) |
| `Invoice` model + `/invoices` | Single amount, no lines, no tenant | **MIGRATE** — new normalized `bss_*` domain; legacy table kept for compatibility |
| `Payment` model + `/payments` | Single-invoice, no idempotency | **DEPRECATE** — replaced by PaymentIntent/Attempt/Transaction/Allocation |
| `record_payment` | Mutates invoice.balance_due directly | **REPLACE** — allocations + ledger + derived status |
| Legacy Django apps | Reference only | **PRESERVE** (documented), not imported |

Problems found (spec §2):
- No tenant isolation on `Invoice`/`Payment`/`Plan` (cross-tenant leakage risk).
- No currency field; no currency enforcement.
- Invoice status mutated directly (`paid`/`partially_paid`) without allocation or
  ledger; no derivation from financial amounts.
- No idempotency / unique constraints on payment capture.
- No gateway abstraction, no webhooks, no signature verification.
- No immutable ledger; balance_due is a mutable projection.
- No refunds / chargebacks / reconciliation / dunning / manual payments / receipts.
- Money is stored as `Numeric(12,2)` (Decimal — good, not float); the new domain
  keeps this invariant and adds an explicit `Money`/currency layer.

## 2. What is preserved
- Existing `Plan`, `Invoice`, `Payment` tables + endpoints (temporary API
  compatibility, spec §35); new financial domain lives in `bss_*` tables.
- Existing `database.py` conventions; per-service outbox/inbox pattern.

## 3. What is implemented (Milestone 4)

- **Money/currency**: integer-minor-unit-safe Decimal handling; currency on every
  monetary record; no cross-currency allocation; no float arithmetic.
- **Immutable double-entry ledger**: `LedgerAccount`, `JournalEntry`, `JournalLine`,
  `AccountingPeriod`, `LedgerBalanceProjection`; balanced entries, reversal
  entries, no editing/deleting of posted entries.
- **Gateway framework**: `PaymentGateway` ABC + `FakePaymentGateway`
  (deterministic, tests only) + `RazorpayGateway` (first production adapter,
  declared HTTP calls); capability discovery; per-tenant `GatewayAccount` with
  encrypted credentials + test/live mode.
- **Payment flow**: server-side amount calculation → `PaymentIntent` (state
  machine) → attempts → capture → immutable `PaymentTransaction` →
  `PaymentAllocation` (partial/multiple/overpayment→credit) → ledger posting →
  invoice status derived from allocations → `Receipt` → events → restoration
  eligibility.
- **Webhooks**: raw-body preservation, signature verification before parsing,
  gateway event-id dedup (unique constraint), redacted payload storage,
  asynchronous retry-safe processing.
- **Refunds / chargebacks / disputes**: new immutable events + ledger postings;
  chargeback never deletes the original payment.
- **Manual payments**: `DRAFT→SUBMITTED→UNDER_REVIEW→APPROVED→POSTED` with
  maker-checker; `REJECTED`/`REVERSED`.
- **Reconciliation**: transaction + settlement reconciliation with deterministic
  matching rules, batches/items/exceptions, duplicate-import protection.
- **Dunning**: versioned `DunningPolicy`/`DunningPolicyVersion`/`DunningStage`/
  `DunningCase`/`DunningAction` engine with grace periods, promise-to-pay,
  holds, exemptions; suspension via `billing.suspension_required.v1` events
  (OSS creates the order — BSS never touches RouterOS/AAA directly).
- **Restoration**: eligibility rules that remove only the financial restriction;
  published via `billing.restoration_eligible.v1`.
- **Events**: `bss.events.v1` transactional outbox + inbox; payment/dunning/
  reconciliation event contracts.
- **APIs**: `/api/bss/*` customer + internal + dunning + reporting endpoints.
- **Reports**: derived from ledger projections (collections, aging, refunds,
  chargebacks, settlement, dunning, suspension/restoration).
- **Migration**: `0001_bss_milestone4.py` (additive `bss_*` tables).

## 4. Compliance scope
Payment-data minimization (hosted checkout, tokens, no PAN/CVV), signed webhooks,
encrypted gateway credentials, RBAC, audit. Tax/GST/PCI/RBI treatment is exposed
as tenant-configurable compliance constraints for professional review — no
regulatory rules are invented.

## 5. Final verification report (spec §37)

- **What already existed (preserved):** minimal `Plan`/`Invoice`/`Payment`
  tables + endpoints (kept for temporary API compatibility, spec §35); Decimal
  (Numeric) money storage; `legacy/` Django reference apps (documented, not
  wired into the service).
- **What was broken (repaired):** no tenant isolation on financial records;
  no currency; invoice status patched directly without allocation/ledger;
  single-invoice payments with no idempotency; no gateway/webhook/reconciliation/
  dunning/refunds/chargebacks; no immutable ledger; no manual-payment controls.
- **Schema/migrations:** `0001_bss_milestone4.py` adds 34 `bss_*` tables
  (billing accounts, invoices+lines, payment intents/attempts/transactions/
  allocations, credit notes, receipts, gateway accounts/webhooks, ledger
  accounts/journal entries/lines/periods/balances, refunds, disputes, manual
  payments, settlements+lines, reconciliation batches/items/exceptions, dunning
  policies/versions/stages/cases/actions, promise-to-pay, collection holds,
  outbox/inbox, tenants).
- **Gateway adapters:** `PaymentGateway` ABC + capability discovery;
  `RazorpayGateway` (first production adapter; live calls only when
  `BSS_GATEWAY_LIVE=true`); `FakePaymentGateway` (tests only). Per-tenant
  gateway accounts with encrypted credentials + test/live mode.
- **Webhook security:** raw-body preserved, HMAC signature verified before
  parsing, gateway event-ID dedup (unique constraint), redacted payloads,
  asynchronous retry-safe processing, unknown-event handling.
- **Ledger design:** immutable double-entry subledger (balanced, >= 2 lines,
  one currency, correlation id, period, actor, source event); corrections via
  reversal entries; balances are derived projections with a rebuild tool.
- **Reconciliation rules:** deterministic priority matching (exact external id,
  gateway order+amount, UTR+amount, invoice/account+amount+date, manual review);
  transaction + settlement reconciliation; duplicate-import protection;
  exception queue with resolution.
- **Dunning rules:** versioned policies (published versions immutable), ordered
  stages with delays/action types, grace periods, promise-to-pay, holds,
  exemptions, pause/resume, tenant-scoped, schedule-aware, idempotent.
- **Suspension/restoration flow:** BSS publishes `billing.account_delinquent.v1`,
  `billing.suspension_required.v1`, `billing.restoration_eligible.v1`; OSS
  creates orders; BSS never touches RouterOS/AAA directly. Restoration removes
  only the financial restriction; unrelated holds are never overridden.
- **Tests executed:** `services/bss-service` — **44 passed** (payment, webhook,
  allocation, ledger, refund/chargeback, manual payment, reconciliation,
  dunning, API end-to-end, tenant isolation). No live payment executed.
- **External gateway configuration still required:** Razorpay account + API
  keys + webhook secret + webhook URL (see `docs/payments-gateway-setup.md`);
  sandbox only for integration tests.
- **Compliance items requiring professional review:** tax/GST rates, RBI
  payment-aggregator guidelines, PCI DSS assessment, ERP export mapping, data
  retention (see `docs/payments-security-checklist.md`).
- **Genuine remaining blockers:** none for the implemented scope; live Razorpay
  transport is exercised only in an explicitly configured sandbox
  (`BSS_GATEWAY_LIVE=true`).
