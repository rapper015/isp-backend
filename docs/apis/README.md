# API Documentation — Milestone Index

This folder documents the **complete HTTP API surface** of the platform,
organized by milestone. Each milestone builds on the previous ones; the API
surface is cumulative — earlier milestones remain live on the same services.

## How this is kept up to date

Each milestone doc is generated from the actual route registrations in the
service code (`@app.*` / `@router.*` decorators). When adding or changing an
endpoint, update the corresponding milestone file so it always reflects the
current code ("old one is the new base").

## Milestone files

| Milestone | Service / Context | File |
| --- | --- | --- |
| 0 | **AAA — NAS & RADIUS** (RouterOS, NAS orchestration, RADIUS auth/accounting, sessions, IP pools, radius servers) | [`milestone-0-aaa-nas-radius.md`](milestone-0-aaa-nas-radius.md) |
| 1 | **CRM — Customer Lifecycle** (leads, customers, contacts, addresses, KYC, CAF, risk, timeline, audit) | [`milestone-1-crm.md`](milestone-1-crm.md) |
| 2 | **OSS — Service Orders & Provisioning** (event-sourced orders, workflows/sagas, resources, subscriptions) | [`milestone-2-oss.md`](milestone-2-oss.md) |
| 3 | **Network Control** (policies, sessions, control actions/CoA, RouterOS managed config, FUP, QoS, IP identity) — mounted on `aaa-service` | [`milestone-3-network-control.md`](milestone-3-network-control.md) |
| 4 | **BSS — Billing & Payments** (billing accounts, invoices, payment intents, webhooks, refunds, reconciliation, dunning, ledger, reports) | [`milestone-4-bss.md`](milestone-4-bss.md) |
| 7 | **Device Management — TR-069 CPE Control Plane** (device identity/onboarding, vendor-neutral profiles, verified configuration jobs, drift, controlled actions, diagnostics, firmware canary rollouts, ACS instances) | [`milestone-7-device-management.md`](milestone-7-device-management.md) |
| 8 | **Tenancy — Franchise & Multi-Tenant Management** (tenant registry/lifecycle/provisioning, tenant config & branding, org hierarchy, partners/franchises, scoped RBAC + SoD, service accounts, commissions, settlements, wallets, tenant-aware reports + platform aggregates) | [`milestone-8-tenancy.md`](milestone-8-tenancy.md) |

## Conventions

- **Auth**: `/api/*` routes use internal-service auth. Headers differ by service:
  - `aaa-service`: `X-AAA-Service-Key`
  - `crm-service`: `X-CRM-Service-Key` (management JWT fallback)
  - `oss-service` / `bss-service`: management JWT / `X-BSS-Service-Key` (internal)
  - `device-management-service`: management JWT (`DEVICE_MANAGEMENT_JWT_SECRET`, RBAC per endpoint) /
    `X-Internal-API-Key` (inbound event ingestion)
  - `tenancy-service`: management JWT (`TENANCY_JWT_SECRET`, RBAC per endpoint) /
    `X-Internal-API-Key` (inbound event ingestion)
- **Tenant scoping**: `tenant_id` is passed as a query parameter or in the JSON
  body; all reads/writes are tenant-isolated. The device-management and
  tenancy services additionally validate the requested tenant against the
  authenticated JWT principal; the tenancy service fails closed when context is
  missing.
- **Idempotency**: financial and provisioning endpoints accept `idempotency_key`.
- **Correlation**: responses include `X-Correlation-Id`.
