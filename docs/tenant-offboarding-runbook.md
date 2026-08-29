# Tenant Offboarding Runbook — Milestone 8

Offboarding is controlled, audited and **recoverable**. Tenant databases are
never immediately deleted; archival and retention policies apply.

## 1. Validate authority & contract state

`POST /api/tenancy/tenants/{id}/offboard` requires an elevated platform
permission (`tenants.offboard`) and a reason. The tenant moves to
`OFFBOARDING`.

## 2. Freeze writes as configured

Restrict new-customer onboarding (`restrict`) and optionally billing/service
writes per the contract. Do not disconnect subscribers merely because a
subscription invoice is overdue unless an explicitly approved policy requires it.

## 3. Complete/cancel pending workflows

Pending configuration jobs, orders, workforce jobs and settlements should be
completed or cancelled under control.

## 4. Generate data export

`POST /api/tenancy/tenants/{id}/reports/exports` queues a background export;
verify export integrity (checksums) before proceeding.

## 5. Revoke access

Revoke API credentials and staff memberships; disable service accounts and
integrations.

## 6. Preserve required records

Audit, financial (ledger, settlements, statements) and regulatory records are
preserved. Append-only audit entries document the offboarding.

## 7. Archive (recoverable)

`POST /api/tenancy/tenants/{id}/archive` moves the tenant to `ARCHIVED`.
Deletion is scheduled only where legally/contractually permitted, and always
after the retention period.

## 8. Evidence

Every step emits `tenancy.tenant.offboarding_started.v1` / `tenancy.tenant.archived.v1`
and append-only audit rows (actor, reason, correlation id).
