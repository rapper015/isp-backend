# Milestone 8 — Tenancy Service: Franchise & Multi-Tenant Management API

Service: `tenancy-service`. Auth: management JWT (`TENANCY_JWT_SECRET`) with RBAC for all `/api/tenancy/*`; `X-Internal-API-Key` for inbound event ingestion. Tenant-owned operations require a validated `TenantContext` — `tenant_id` query parameters are reconciled against the authenticated JWT principal and any conflict is rejected (missing context fails closed).

## Health / status

| Method | Path | Description |
| --- | --- | --- |
| GET | `/health` | Liveness probe |
| GET | `/status` | Service phase/status |

## Tenant administration & lifecycle

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/tenancy/tenants` | Create a tenant request (unique code; publishes `tenancy.tenant.requested.v1`) |
| POST | `/api/tenancy/tenants/{id}/validate` | Validate tenant request |
| POST | `/api/tenancy/tenants/{id}/provision` | Run the idempotent provisioning saga → ACTIVE (never active before verification) |
| GET | `/api/tenancy/tenants/{id}/provision` | Provisioning progress |
| GET | `/api/tenancy/tenants` | List tenants (tenant-scoped; platform sees all) |
| GET | `/api/tenancy/tenants/{id}` | Tenant detail |
| POST | `/api/tenancy/tenants/{id}/activate` | Activate (saga) |
| POST | `/api/tenancy/tenants/{id}/suspend` | Suspend (elevated; reason + restriction scope) |
| POST | `/api/tenancy/tenants/{id}/resume` | Resume |
| POST | `/api/tenancy/tenants/{id}/restrict` | Restrict (new-customer onboarding hold) |
| POST | `/api/tenancy/tenants/{id}/offboard` | Start offboarding (elevated; reason required) |
| POST | `/api/tenancy/tenants/{id}/archive` | Archive (recoverable; never deletes DB) |
| GET | `/api/tenancy/tenants/{id}/health` | Tenant + provisioning health checks |

## Tenant configuration, domains, features, entitlements, quotas, secrets

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/tenancy/tenants/{id}/config?category=` | Read versioned tenant config |
| PUT | `/api/tenancy/tenants/{id}/config` | Update versioned config (legal/locale/tax/invoice/portal/…) |
| POST | `/api/tenancy/tenants/{id}/domains` | Add custom domain (verification token returned; not trusted until verified) |
| POST | `/api/tenancy/tenants/{id}/domains/{domain_id}/verify` | Verify domain ownership |
| POST | `/api/tenancy/tenants/{id}/features` | Set feature flag (not authorization) |
| POST | `/api/tenancy/tenants/{id}/entitlements` | Grant entitlement (what the tenant licensed) |
| POST | `/api/tenancy/tenants/{id}/quotas` | Set tenant quota |
| POST | `/api/tenancy/tenants/{id}/secrets` | Store an encrypted integration secret |

## Organization units

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/tenancy/tenants/{id}/org-units` | Create org unit (legal entity / franchise / branch / team; validated materialized path) |
| POST | `/api/tenancy/tenants/{id}/org-units/{unit_id}/reparent` | Reparent (circular/cross-tenant/silent-scope changes prevented) |
| GET | `/api/tenancy/tenants/{id}/org-units` | List org units (optionally under a parent) |

## Partners / franchises

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/tenancy/tenants/{id}/partners` | Create a partner (franchise/reseller/distributor/managed operator/…) |
| POST | `/api/tenancy/tenants/{id}/partners/{pid}/status` | Partner lifecycle (PROSPECT→ONBOARDING→ACTIVE→…) |
| POST | `/api/tenancy/tenants/{id}/partners/{pid}/link` | Link partner hierarchy (cycle prevented) |
| POST | `/api/tenancy/tenants/{id}/partners/{pid}/agreements` | Create partner agreement |
| POST | `/api/tenancy/tenants/{id}/agreements/{aid}/versions` | Add versioned agreement terms |
| POST | `/api/tenancy/tenants/{id}/partners/{pid}/service-scopes` | Enable/disable partner service scope |
| POST | `/api/tenancy/tenants/{id}/partners/{pid}/territories` | Assign territories |
| POST | `/api/tenancy/tenants/{id}/partners/{pid}/memberships` | Add partner user membership |
| GET | `/api/tenancy/tenants/{id}/partners` | List partners |

## Customer/service ownership, transfers, grants

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/tenancy/tenants/{id}/ownership` | Set explicit ownership (acquisition/servicing/billing/support/network/collection) |
| POST | `/api/tenancy/tenants/{id}/transfers` | Request a customer transfer (validated, history preserved) |
| POST | `/api/tenancy/tenants/{id}/transfers/{tid}/approve` | Approve transfer (maker-checker) |
| POST | `/api/tenancy/tenants/{id}/grants` | Create an explicit data-access grant (default: no cross-unit sharing) |

## Access control (memberships, roles, approvals, service accounts, impersonation)

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/tenancy/tenants/{id}/memberships` | Create tenant membership |
| POST | `/api/tenancy/tenants/{id}/roles` | Create a tenant role |
| PUT | `/api/tenancy/tenants/{id}/roles/{rid}/permissions` | Set role permissions |
| POST | `/api/tenancy/tenants/{id}/role-assignments` | Scoped assignment (role × org unit × scope kind) |
| GET | `/api/tenancy/permissions` | Global permission catalogue |
| POST | `/api/tenancy/tenants/{id}/approvals` | Request a maker-checker approval |
| POST | `/api/tenancy/tenants/{id}/approvals/{aid}/decide` | Approve/reject (separation-of-duty enforced) |
| POST | `/api/tenancy/tenants/{id}/service-accounts` | Create a scoped service account |
| POST | `/api/tenancy/tenants/{id}/api-credentials` | Issue an API credential (secret returned once, encrypted at rest) |
| POST | `/api/tenancy/tenants/{id}/impersonation` | Start a platform-admin impersonation session (reason/ticket, read-only default, TTL, audited) |

## Commissions

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/tenancy/tenants/{id}/commission-plans` | Create a commission plan |
| POST | `/api/tenancy/tenants/{id}/commission-plans/{pid}/approve` | Approve a plan (elevated) |
| POST | `/api/tenancy/tenants/{id}/commission-plans/{pid}/rules` | Add a versioned rule (controlled engine only) |
| POST | `/api/tenancy/tenants/{id}/commission-agreements` | Bind a partner to an approved plan |
| POST | `/api/tenancy/tenants/{id}/commission-earnings` | Recognize an earning from a basis event (idempotent, reproducible) |
| POST | `/api/tenancy/tenants/{id}/commission-earnings/{eid}/clawback` | Create an immutable clawback (never deletes the earning) |
| POST | `/api/tenancy/tenants/{id}/commission-earnings/{eid}/adjust` | Create an adjustment |
| GET | `/api/tenancy/tenants/{id}/commission-earnings` | List earnings |

## Settlements

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/tenancy/tenants/{id}/settlement-cycles` | Create a settlement cycle |
| POST | `/api/tenancy/tenants/{id}/settlements` | Create a partner settlement |
| POST | `/api/tenancy/tenants/{id}/settlements/{sid}/calculate` | Calculate (idempotent; no duplicate lines) |
| POST | `/api/tenancy/tenants/{id}/settlements/{sid}/review` | Submit for review |
| POST | `/api/tenancy/tenants/{id}/settlements/{sid}/approve` | Approve (elevated, SoD) |
| POST | `/api/tenancy/tenants/{id}/settlements/{sid}/lock` | Lock (immutable thereafter) |
| POST | `/api/tenancy/tenants/{id}/settlements/{sid}/statement` | Generate partner statement |
| POST | `/api/tenancy/tenants/{id}/settlements/{sid}/payout` | Record payout (posts ledger entry) |
| POST | `/api/tenancy/tenants/{id}/settlements/{sid}/reconcile` | Reconcile |
| POST | `/api/tenancy/tenants/{id}/settlements/{sid}/disputes` | Open a dispute |
| POST | `/api/tenancy/tenants/{id}/disputes/{did}/resolve` | Resolve a dispute (creates adjustment reference) |
| POST | `/api/tenancy/tenants/{id}/settlements/{sid}/reverse` | Reverse a settlement (elevated) |
| GET | `/api/tenancy/tenants/{id}/settlements` | List settlements |

## Wallets

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/tenancy/tenants/{id}/partners/{pid}/wallet` | Wallet balance (ledger projection) |
| POST | `/api/tenancy/tenants/{id}/wallets/{wid}/entries` | Post an immutable wallet movement (mirrors to ledger) |

## Reports, aggregates, exports, audit

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/tenancy/tenants/{id}/reports` | Generate a tenant/franchise/branch report (authorized scope only) |
| GET | `/api/tenancy/reports/aggregate` | Authorized platform-wide aggregate (privacy-preserving, freshness stamped) |
| POST | `/api/tenancy/tenants/{id}/reports/exports` | Queue a background export |
| GET | `/api/tenancy/audit` | Append-only audit log |

## Events

Published (`tenancy.*`): tenant.requested, tenant.provisioned, tenant.activated,
tenant.restricted, tenant.suspended, tenant.resumed, tenant.offboarding_started,
tenant.archived, partner.created, partner.status_changed, membership.changed,
role.changed, feature.changed, domain.changed, impersonation.started,
commission.earning, commission.clawback, settlement.approved, settlement.locked,
settlement.paid, wallet.entry, customer.transferred, ownership.changed.

Consumed (idempotent, tenant-validated): `billing.payment.captured.v1`,
`billing.payment.refunded.v1`, `billing.invoice.issued.v1`,
`crm.customer.activated.v1`, `oss.order.activated.v1`.
