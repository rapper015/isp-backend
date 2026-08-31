# Tenancy Service (Milestone 8)

Franchise & Multi-Tenant Management — the platform's central control plane.

- **Tenant registry & lifecycle**: request → validate → provision (idempotent
  saga) → active → restrict/suspend/resume → offboard → archive.
- **Tenant configuration**: versioned config, domains (verified), branding,
  feature flags, entitlements, quotas, encrypted secrets.
- **Organization hierarchy & partners**: org units (legal entity / franchise /
  branch / team) with validated materialized paths; partners (franchise,
  reseller, distributor, managed operator, collection/field/network partner)
  with agreements, territories, service scopes and lifecycle.
- **Customer/service ownership**: explicit ownership roles, controlled
  transfers, data-access grants.
- **Scoped RBAC**: permission registry, role templates, tenant roles, scoped
  assignments, separation of duty, maker-checker approvals, service accounts,
  API credentials, audited impersonation.
- **Commissions**: versioned plans/rules, deterministic engine, immutable
  earnings, clawbacks/adjustments.
- **Settlements**: cycles, idempotent calculation, review/approval/lock,
  statements, payouts, reconciliation, disputes; partner wallets backed by an
  immutable ledger.
- **Reporting**: tenant/franchise reports, authorized platform aggregates,
  exports.

## Run

```bash
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r ..\..\shared\runtime\requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
```

Worker: `.\.venv\Scripts\python.exe -m app.worker_runner`

## Tests

```bash
.\.venv\Scripts\python.exe -m pytest -q
```

## Isolation

Tenant-owned data is only reachable through a validated `TenantContext`; access
without context raises `TenantContextRequiredError` (fail closed). The database
router never silently falls back to a default tenant database.
