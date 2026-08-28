# Milestone 4 — Reconciliation Runbook

## Concepts

- **Transaction reconciliation** compares imported gateway transaction rows with
  internal `PaymentTransaction` records.
- **Settlement reconciliation** compares gateway settlements (net = captures -
  refunds - fees) with imported settlement lines.
- Payment capture and gateway settlement are **separate** states; a customer
  payment is confirmed at capture, settled later by the acquirer.

## Matching rules (deterministic priority)

1. `EXACT_EXTERNAL_ID` — internal transaction external_ref == gateway row id.
2. `GATEWAY_ORDER_PLUS_AMOUNT` — gateway order ref + exact amount.
3. `UTR_PLUS_AMOUNT` — bank reference + amount (manual import).
4. `INVOICE_REFERENCE_PLUS_AMOUNT_DATE` — invoice + amount + date window.
5. `ACCOUNT_REFERENCE_PLUS_AMOUNT_DATE` — account + amount + date window.
6. `MANUAL_REVIEW` — weak candidates go to the exception queue for a human.

Weak candidates with multiple possible matches are **not** auto-matched.

## Daily workflow

1. `POST /api/bss/reconciliation/batches` — create a batch and import gateway
   rows (or gateway report). Duplicate imports are ignored.
2. `POST /api/bss/reconciliation/batches/{id}/run` — run deterministic matching.
3. `GET /api/bss/reconciliation/exceptions` — review exceptions
   (amount mismatch, missing transaction, missing settlement, duplicate,
   unexpected fee, missing refund, chargeback mismatch, unknown bank credit).
4. `POST /api/bss/reconciliation/exceptions/{id}/resolve` — record resolution.
5. `POST /api/bss/reconciliation/settlements` — import gateway settlement
   (duplicate settlement_reference ignored), then reconcile a SETTLEMENT batch.

## Exception types surfaced

`AMOUNT_MISMATCH`, `SETTLEMENT_MISMATCH`, plus the classification categories
documented in `revenue/reconciliation.py`. All exceptions are reviewable and
require explicit resolution (maker-checker where configured).

## Safety

- Importing the same gateway/bank report twice does not duplicate transactions.
- Reconciliation never mutates ledger entries; it only matches and flags.
- Batch reopen/close is authorization-gated.
