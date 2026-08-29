# Tenant Migration Runbook — Milestone 8

Migrating existing (legacy/single-operator) data into the tenancy model is
high-risk. This runbook follows the staged approach; **never assign ambiguous
records to a default tenant to complete the migration**.

## 1. Inventory tenant-owned models

Across the modern services (crm/bss/oss/aaa/device/support/workforce) every
tenant-scoped table carries a `tenant_id`. Legacy Django tables
(`customers.*`, `billing.*`, `subscribers.*`, `plans.*`, `orders.*`,
`resellers.*`) are global and need ownership mapping.

## 2. Identify existing franchise/branch ownership

Legacy `resellers.Franchise`/`Branch` and `customers.Franchise` plus modern
`crm_franchises`/`crm_branches` are the ownership source. Map them to canonical
`ten_tenants` + `ten_partners` (franchise) + `ten_organization_units` (branch).

## 3. Create canonical tenant records

```http
POST /api/tenancy/tenants
```
for each ISP/business entity, then provision them ACTIVE.

## 4. Backfill tenant context

Backfill `tenant_id` on tenant-owned rows from ownership rules. Records that
cannot be attributed to exactly one tenant are **quarantined** (flagged, not
silently assigned).

## 5. Validation reports + constraints

Generate per-table record counts + checksums, then add `NOT NULL`/FK
constraints only after data is clean.

## 6. Tenant-aware reads then writes

Introduce scoped reads first (fail-closed selectors), verify, then scoped
writes. Migrate caches (tenant-prefixed keys), files (tenant prefixes), and
background tasks (explicit tenant scope) before enforcing.

## 7. Database-per-tenant tier (target)

Where moving a tenant to its own database:
1. Create the tenant database (`TenantDatabase` record, state READY).
2. Run migrations against it.
3. Copy scoped records in dependency order, preserving UUIDs and legacy refs.
4. Verify counts, checksums, financial ledger balance, cross-entity refs,
   file ownership and event/outbox state.
5. Run tenant isolation tests against the new database.
6. Controlled cutover with rollback capability; record migration evidence.
7. **Do not** delete source data during initial migration — archive only after
   verification and retention approval.

## 8. Enable enforcement + remove legacy paths

After verification, enable enforcement and remove unsafe legacy read paths.
Ambiguous records stay quarantined and are reported.
