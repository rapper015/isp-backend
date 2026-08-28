# Milestone 4 — Financial Operations Runbook

## Money and currency

- All money is `Decimal` (Numeric) with an explicit currency; binary floats are
  rejected (`revenue/money.py`). INR is first-class (2 decimal places / paise).
- Cross-currency allocation and implicit conversion are forbidden.

## Ledger

- Immutable double-entry ledger (`revenue/ledger.py`): balanced entries with
  >= 2 lines, one currency, correlation id, period, actor, source event.
- Posted entries are never edited or deleted; corrections use **reversal**
  entries. Balances are derived projections (`rebuild-projection` endpoint).
- Operational ledger is not the statutory ERP; export/import integration points
  are provided for the ISP's accounting system.

## Payment lifecycle

1. Server computes payable; PaymentIntent (state machine) is created with a
   unique idempotency key.
2. Hosted checkout creates the gateway order; the callback is treated as
   **provisional**.
3. Confirmation happens only through a **verified, deduplicated webhook** or
   gateway API; a `PaymentTransaction` is posted once.
4. Allocation (oldest-invoice-first) enforces
   `sum(allocations) + credit <= confirmed amount`; overpayment becomes credit.
5. Invoice status is **derived** from allocations (`ISSUED → PARTIALLY_PAID →
   PAID`); a capture event never patches status directly.
6. Ledger entries + Receipt (references the immutable transaction) are created;
   financial events are published.

## Refunds & chargebacks

- Refund amount must be <= refundable (captured - already refunded); partial and
  multiple partial refunds supported; allocation reversal + reversal ledger.
- A chargeback is a **new** immutable event and posting; the original payment is
  never deleted; the account is placed on financial hold per policy.

## Manual / offline payments

- `DRAFT → SUBMITTED → UNDER_REVIEW → APPROVED → POSTED` (maker-checker);
  amounts above the tenant threshold (or cash/cheque) always require approval.
- A submitted manual payment never restores service unless approval is complete.
- `REJECTED` / `REVERSED` are explicit outcomes.

## Dunning

- Versioned `DunningPolicy` (published versions immutable) with ordered stages,
  delays, action types; cases are event-driven, schedule-aware, idempotent,
  pausable/resumable, tenant-scoped.
- Supports grace periods, promise-to-pay, collection/dispute/legal/VIP holds,
  exemptions, minimum overdue, escalation limits.
- Suspension is **never** executed by BSS: it publishes
  `billing.suspension_required.v1`; OSS creates the idempotent suspension order
  and Network Control/AAA enforces it. BSS never sends RouterOS commands or
  edits RADIUS/AAA state.

## Restoration

- A confirmed, allocated payment triggers `evaluate_restoration`: it clears only
  the financial restriction it resolves and publishes
  `billing.restoration_eligible.v1` when eligible. It never overrides fraud /
  administrative / compliance / chargeback holds or terminated services.
- Financial success and operational restoration are separate outcomes joined by
  a durable workflow; a valid payment is never reversed because restoration failed.

## Race-condition handling

- Idempotency keys + unique constraints (intent, transaction external_ref,
  webhook event id, allocation) prevent duplicate financial effects.
- Out-of-order webhooks are safe: confirmation only posts once; a refund or
  chargeback after restoration posts new events without deleting the payment.

## Reports

Derived from financial records and ledger projections: daily collections,
invoice aging, payment methods, refunds, chargebacks, settlements, reconciliation
exceptions, credit balances, outstanding, ledger balances. No mutable dashboard
counters.
