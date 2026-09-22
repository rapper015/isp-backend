# API Documentation — Milestone Index

This folder documents the **complete HTTP API surface** of the platform,
organized by milestone. Each milestone builds on the previous ones; the API
surface is cumulative — earlier milestones remain live on the same services.

## How this is kept up to date

Each milestone doc is generated from the actual route registrations in the
service code (`@app.*` / `@router.*` decorators). When adding or changing an
endpoint, update the corresponding milestone file so it always reflects the
current code ("old one is the new base").

## One-file frontend handoff per milestone

Use **only one file** from this table for a milestone. Each is the canonical
frontend handoff: public gateway path, authentication, tenant rules, screens,
endpoint map, request/workflow rules, async behaviour, and deployment limits
are kept together. No companion milestone document is required.

| Milestone | Single frontend handoff file | Scope |
| --- | --- | --- |
| 0 | [`milestone-0-aaa-nas-radius.md`](milestone-0-aaa-nas-radius.md) | Operator auth, platform administration, AAA browser integration. |
| 1 | [`milestone-1-crm.md`](milestone-1-crm.md) | CRM customer lifecycle. |
| 2 | [`milestone-2-oss.md`](milestone-2-oss.md) | OSS orders, provisioning, resources, subscriptions. |
| 3 | [`milestone-3-network-control.md`](milestone-3-network-control.md) | Policy, sessions, QoS, FUP, network control. |
| 4 | [`milestone-4-bss.md`](milestone-4-bss.md) | Billing, checkout, payments, refund, reconciliation. |
| 5 | [`milestone-5-frontend-integration.md`](milestone-5-frontend-integration.md) | Support console/portal and deployment requirement. |
| 6 | [`milestone-6-frontend-integration.md`](milestone-6-frontend-integration.md) | Workforce dispatch and field operations. |
| 7 | [`milestone-7-device-management.md`](milestone-7-device-management.md) | CPE/device management. |
| 8 | [`milestone-8-tenancy.md`](milestone-8-tenancy.md) | Tenancy, franchise, RBAC, settlement. |
| 9 | [`milestone-9-assurance.md`](milestone-9-assurance.md) | Assurance, alerts, incidents, SLOs. |
| 10 | [`milestone-10-intelligence.md`](milestone-10-intelligence.md) | Intelligence, models, AI governance, remediation. |

## Docker gateway versus service-internal paths

Only the `gateway` is published on port `4000`. The current full-platform
Docker configuration exposes these public path prefixes:

| Public path | Destination |
| --- | --- |
| `/api/v1/auth/*`, `/api/v1/platform/*` | Platform Core |
| `/api/aaa/*` | AAA service |
| `/api/crm/*`, `/api/v1/crm/*` | CRM service |
| `/api/oss/*`, `/api/v1/oss/*` | OSS service |
| `/api/bss/*`, `/api/v1/bss/*` | BSS service |
| `/api/nms/*`, `/api/v1/nms/*` | NMS service |
| `/api/v1/ipam/*` | IPAM service |
| `/api/siem/*`, `/api/v1/siem/*` | SIEM service |
| `/api/workforce/*`, `/api/v1/workforce/*` | Workforce service |
| `/api/warehouse/*`, `/api/v1/warehouse/*` | Data warehouse service |
| `/api/v1/aiops/*` | AIOps service |
| `/api/device-management/*`, `/api/v1/device-management/*` | Device management service |
| `/api/tenancy/*`, `/api/v1/tenancy/*` | Tenancy service |
| `/api/assurance/*`, `/api/v1/assurance/*` | Assurance service |
| `/api/intelligence/*`, `/api/v1/intelligence/*` | Intelligence service |
| `/api/v1/customers*` | CRM legacy compatibility routes |

Support is currently **not** mounted at the gateway. Service `/health`,
`/status`, `/internal/*`, `/api/nas/*`, and legacy CRM paths such as
`/customers` are not exposed through the Docker gateway. Run health probes
inside the Docker network; `GET /health` on port 4000 returns 404.

## Conventions

- **Auth**: `/api/*` routes use internal-service auth. Headers differ by service:
  - `aaa-service`: `X-AAA-Service-Key`
  - `crm-service`: `X-CRM-Service-Key` (management JWT fallback)
  - `oss-service` / `bss-service`: management JWT / `X-BSS-Service-Key` (internal)
  - `device-management-service`: management JWT (`DEVICE_MANAGEMENT_JWT_SECRET`, RBAC per endpoint) /
    `X-Internal-API-Key` (inbound event ingestion)
  - `tenancy-service`: management JWT (`TENANCY_JWT_SECRET`, RBAC per endpoint) /
    `X-Internal-API-Key` (inbound event ingestion)
  - `assurance-service`: management JWT (`ASSURANCE_JWT_SECRET`, RBAC per endpoint, elevated
    permissions for incident close / postmortem / maintenance approve / platform aggregate) /
    `X-Internal-API-Key` (inbound event + alert + observation ingestion)
  - `intelligence-service`: management JWT (`INTELLIGENCE_JWT_SECRET`, RBAC per endpoint, elevated
    permissions for model approve/deploy/rollback, kill switch, remediation manage, fraud manage,
    platform aggregate) / `X-Internal-API-Key` (inbound event ingestion)
- **Tenant scoping**: `tenant_id` is passed as a query parameter or in the JSON
  body; all reads/writes are tenant-isolated. The device-management and
  tenancy services additionally validate the requested tenant against the
  authenticated JWT principal; the tenancy service fails closed when context is
  missing.
- **Idempotency**: financial and provisioning endpoints accept `idempotency_key`.
- **Correlation**: responses include `X-Correlation-Id`.
