# Backup & Restore Runbook — Milestone 8

Recovery objectives are **configurable operational targets**, not invented
guarantees. This runbook documents the controls; infrastructure teams set RPO/RTO
per environment.

## 1. What to back up

- **Control plane** (`tenancy` database): tenant registry, lifecycle,
  configuration, domains, partners, RBAC, commissions, settlements, wallets
  (ledger), reports/aggregates, audit index.
- **Data plane** (per-service databases): operational tenant data (crm, bss,
  oss, aaa, device, support, workforce). Each service already owns its database.

## 2. Tenant-specific backup

- Backup the control plane (single logical database).
- For the `DATABASE_PER_TENANT` tier, snapshot each tenant database separately so
  a restore never overwrites another tenant.
- Encrypt backups; verify integrity (checksums) and record an audit entry.

## 3. Restore

- **Tenant-specific restore** into an isolated verification environment first.
- Verify record counts, financial ledger balance, and run tenant isolation
  tests before cutover.
- A tenant restore must never touch another tenant's database or rows.

## 4. Point-in-time recovery

- Where the infrastructure supports PITR (PostgreSQL WAL), document the window
  and test it. The ledger is immutable, so financial state can always be
  re-derived from journal entries if needed.

## 5. Retention & recovery testing

- Configure retention per data class (financial records preserved per policy).
- Run recovery drills per environment; record results in the audit log.
