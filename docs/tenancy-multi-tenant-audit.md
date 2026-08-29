# Milestone 8 — Franchise & Multi-Tenant Management: Architecture Audit

Service: `tenancy-service` (new control plane). Date: 2026-08-30.

## 1. What already exists

| Area | Exists | Location |
| --- | --- | --- |
| Tenant model | Per-service local `Tenant` copies | `crm_tenants`, `bss_tenants`, `oss_tenants`, `aaa_tenants`, `device_tenants` (each service's `app/models/*.py`) |
| Tenant scoping | Hand-rolled JWT-claim validation per service | each service `security.py`; device-mgmt has the only `current_tenant` ContextVar |
| Franchise / Branch | Modern + legacy | `crm_franchises`/`crm_branches` (crm-service), legacy `resellers.Franchise`/`Branch`, `customers.Franchise` |
| Partner / Reseller / Distributor | Reference only | `oss_orders.reseller_id`/`franchise_id` (external string refs); no partner model |
| Commission / Royalty / Settlement (franchise) | **None** | greenfield |
| RBAC | Per-service `ROLE_PERMISSIONS` dicts | each service `security.py`; franchise-scoped roles only in legacy `AdminUser` |
| Financial ledger | Immutable journal (M4) | `bss_ledger_accounts`, `bss_journal_entries`, `bss_journal_lines`, `bss_accounting_periods`, `bss_ledger_balances` (`bss-service/app/revenue/`) |
| Custom domains / white-label / feature flags / entitlements | **None** | greenfield |
| Service-account / internal auth | Per-service `X-*-Service-Key` / `X-Internal-API-Key`; **no tenant context on internal calls** | each service `security.py` |
| Events | Runtime envelopes carry `tenant_id`; **`shared/contracts/event-envelope.schema.json` is stale (omits `tenant_id`, `additionalProperties:false`)** | each service `events.py` + `shared/contracts/` |
| Workers | Mixed scoping: device-mgmt per-tenant; crm semi-global; oss/aaa global sweeps | each service `worker_runner.py`/`tasks.py` |
| Isolation tests | Partial | device-mgmt `test_multitenancy.py`, aaa/bss/crm isolation tests |

## 2. Classification

| Component | Decision | Notes |
| --- | --- | --- |
| Per-service `Tenant` tables | `KEEP` | Existing services keep their local tenant records; the tenancy-service becomes the canonical registry they converge on |
| Device-mgmt `current_tenant` ContextVar | `EXTEND` | Generalize into the authoritative `TenantContext` contract |
| Per-service JWT `tenant_id` claim | `EXTEND` | Formalize claim names + membership validation in the tenancy-service |
| `crm_franchises` / `crm_branches` | `EXTEND` | Map into the canonical organization-unit + partner hierarchy (branch under franchise) |
| Legacy `resellers`/`customers` franchise | `DEPRECATE` | Preserved (legacy Django), not extended; documented as migration source |
| `oss_orders.franchise_id`/`reseller_id` | `EXTEND` | Become external references to canonical `ten_partners` |
| bss immutable ledger | `REUSE` | Settlement/commission postings reuse the same immutable journal pattern (post/reverse, never edit) |
| bss `BillingAccount.credit_balance` | `KEEP` | Projection, reconciled from ledger; not the source of truth |
| `shared/contracts/event-envelope.schema.json` | `REPAIR` | Add `tenant_id` (nullable) + `correlation_id`/`idempotency_key` so the contract matches runtime envelopes |
| Per-service `security.py` auth | `EXTEND` | Keep; tenancy-service adds service-account + API-credential + impersonation lifecycle |
| nms / ipam / siem / warehouse / aiops | `REFACTOR (documented)` | Not tenant-scoped today; M8 provides the context/scoping contract + fail-closed guidance they must adopt (out of M8 code scope for the stubs) |

## 3. Vulnerability scan (existing code)

- **Tenant supplied by query/body** in crm/oss/bss/aaa — mitigated today only by the JWT fallback comparing the claim; any path that skips JWT auth (internal key only) trusts caller-supplied `tenant_id`.
- **No tenant context on internal service calls** — internal callers pass `tenant_id` explicitly; no signed tenant header.
- **Legacy `billing`/`plans`/`payments` tables are global** (no tenant column).
- **`nms`/`ipam`/`siem`/`warehouse`/`aiops` are unscoped** (global reads/writes).
- **No cross-service tenant catalog** — each service invents its own tenant rows; no provisioning/offboarding control.
- **No franchise financial models** — nothing to corrupt today, but commission/settlement is entirely greenfield and must be built ledger-backed from day one.
- **Stale event schema** — the committed JSON schema would reject runtime envelopes carrying `tenant_id`.

## 4. Selected isolation strategy

**Hybrid control-plane / shared-schema data-plane.**

- **Control plane (`tenancy-service` DB, `ten_` prefix):** tenant registry, lifecycle/provisioning state, tenant configuration, domains/branding, feature flags, entitlements, quotas, organization hierarchy, partners/agreements, RBAC registry + scoped assignments, service accounts, commissions, settlements, partner wallets (ledger-backed), tenant-aware reports + authorized platform aggregates, audit index.
- **Data plane (existing per-service DBs):** the operational tenant data already lives in each service's own DB with mandatory `tenant_id` (crm/bss/oss/aaa/device/support/workforce). Isolation mode is modeled explicitly (`SHARED_SCHEMA_WITH_RLS` for the data plane, `DATABASE_PER_TENANT` supported as a documented higher tier with a clean routing interface). The tenancy-service implements a **fail-closed** tenant-context + database-routing layer: tenant-owned model access without a valid context raises a controlled exception — never a silent default fallback, never an empty queryset.

## 5. Key security invariants (implemented in M8)

- `TenantContext` resolved only from trusted signals (JWT claim + membership validation); conflicting signals reject the request; never from body/query alone.
- Missing tenant context fails closed.
- Database router cannot silently fall back; per-tenant writes are scoped.
- Commission/settlement effects create immutable ledger entries; wallets are derived projections, never editable balances.
- Maker-checker + separation-of-duty for financial actions; elevated permissions for tenant-wide/offboarding/financial operations.
- Platform-admin access is explicit, time-limited, read-only-by-default, fully audited (impersonation sessions).
