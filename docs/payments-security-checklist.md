# Milestone 4 — Security & Compliance Checklist

## Implemented in this milestone

- [x] **No card data stored** — hosted checkout + tokenization; only approved
      metadata (method type, network/brand, last four, expiry where permitted).
- [x] **Webhook signatures verified** before parsing; replay/dedup via unique
      `(tenant, external_event_id)`; raw body preserved; redacted payload stored.
- [x] **Encrypted credentials at rest** — gateway API keys, secrets and webhook
      secrets are Fernet-encrypted (`BSS_ENCRYPTION_KEY`); never returned by APIs.
- [x] **Test/live isolation** — every gateway account and transaction carries a
      `mode`; test transactions never affect live invoices/ledger.
- [x] **RBAC** — BSS roles (`BSS_MANAGER`, `BSS_OPERATOR`, `FINANCE_MANAGER`,
      `AUDITOR`, ...) with scoped permissions; maker-checker for manual payments
      and refund approval.
- [x] **Tenant isolation** — all `bss_*` tables are tenant-scoped; cross-tenant
      reads return 404.
- [x] **Audit** — privileged financial actions (gateway config, manual payments,
      refunds, chargebacks, reconciliation resolution, dunning holds, webhooks)
      are recorded; payment/ledger events carry correlation + actor.
- [x] **Rate limiting** — webhook and management/internal rate limits (fail-open).
- [x] **Idempotency** — intent, capture, webhook, refund, settlement, manual
      payment unique constraints prevent duplicate financial effects.
- [x] **Money** — Decimal only, no floats, currency enforced.

## Items requiring professional review (not invented here)

- [ ] **Tax / GST** treatment and rates — expose as tenant-configurable
      compliance constraints; final review by qualified tax professionals.
- [ ] **RBI payment aggregation / PA-CB guidelines** — gateway + platform
      operating model compliance.
- [ ] **PCI DSS** scope — hosted-checkout integration minimizes scope but the
      deployment and gateway flow must be assessed by a QSA.
- [ ] **Statutory ERP export** — the operational ledger is a subledger; map
      accounts to the ISP's ERP chart of accounts before statutory reporting.
- [ ] **Data residency / retention** — payment and PII retention periods per
      applicable law.
- [ ] **Chargeback/dunning legal boundaries** — service suspension/termination
      rules must be reviewed for regulatory and consumer-protection compliance.

## Operational hygiene

- Rotate `BSS_ENCRYPTION_KEY`, gateway credentials and webhook secrets regularly.
- Keep `BSS_GATEWAY_LIVE=false` except in an explicitly configured sandbox.
- `BSS_AUTO_CREATE_SCHEMA=false` in production; apply Alembic migrations.
- Never place plaintext secrets in code, logs or API responses; webhook payloads
  are redacted before storage.
