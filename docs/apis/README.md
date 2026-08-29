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
| 6 | **Workforce — Field Operations Management** (canonical work orders, appointments, visits, technician profiles, explainable assignment, dispatch, GPS geofence, checklists, proof of work, QA, field SLA, inventory integration, offline sync) | [`milestone-6-workforce.md`](milestone-6-workforce.md) |

## Conventions

- **Auth**: `/api/*` routes use internal-service auth. Headers differ by service:
  - `aaa-service`: `X-AAA-Service-Key`
  - `crm-service`: `X-CRM-Service-Key` (management JWT fallback)
  - `oss-service` / `bss-service`: management JWT / `X-BSS-Service-Key` (internal)
  - `workforce-service`: management JWT (`WORKFORCE_JWT_SECRET`) / technician
    mobile JWT (`WORKFORCE_TECHNICIAN_JWT_SECRET`, role `TECHNICIAN`) / customer
    portal JWT (`WORKFORCE_CUSTOMER_JWT_SECRET`) / `X-Internal-API-Key`
- **Tenant scoping**: `tenant_id` is passed as a query parameter or in the JSON
  body; all reads/writes are tenant-isolated.
- **Idempotency**: financial and provisioning endpoints accept `idempotency_key`.
- **Correlation**: responses include `X-Correlation-Id`.
