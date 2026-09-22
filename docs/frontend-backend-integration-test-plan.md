# Frontend Backend-Integration Test Plan

## Purpose

Use this document to create Plane tasks and to implement the frontend E2E/integration test suite for the Milestone 10 backend deployed at `http://187.127.141.214:4000`.

The goal is to prove the frontend can authenticate, send the correct tenant context, create safe generic records, read them back, display useful errors, and capture visual evidence. Do not hard-code passwords, access tokens, or test IDs in frontend source code.

## Test configuration

Provide these values through the frontend test runner environment (for example, `.env.e2e`, GitHub Actions secrets, or Plane variables):

```dotenv
VITE_API_BASE_URL=http://187.127.141.214:4000
E2E_OPERATOR_USERNAME=harbor_platform_operator
E2E_OPERATOR_PASSWORD=<provided-by-deployment-manager>
E2E_PLATFORM_TENANT_ID=774e8f20-a767-4cb4-bf90-c3ce4ad352f0
E2E_AAA_TENANT_ID=8e19c9fa-60cc-4345-9388-954ca1a3cf41
```

Current generic fixtures on the deployment:

| Fixture | ID / code |
| --- | --- |
| CRM/Platform tenant | `Harborline Communications` — `774e8f20-a767-4cb4-bf90-c3ce4ad352f0` |
| AAA tenant | `Harborline Communications` — `8e19c9fa-60cc-4345-9388-954ca1a3cf41` |
| NAS | `Harbor Gateway One` — `7e7b8691-2b2c-4250-b5c7-d4ff561ce5bd` |
| BSS service catalog | `FIBER_ACCESS_STANDARD` |
| Workforce technician | `Jordan Patel` |
| Workforce work order | `WO-2026-00000001` |
| NMS escalation policy | `Harbor Operations Escalation` |

## Authentication and request rules

1. Log in using `POST /api/v1/auth/login` with `{ "username", "password" }`.
2. Store `access_token` in memory only. Attach it as `Authorization: Bearer <access_token>` to protected requests.
3. Call `GET /api/v1/auth/me` after login. The E2E operator must have a non-null `tenant_id` equal to `E2E_PLATFORM_TENANT_ID`.
4. Do not log tokens, passwords, router credentials, or API keys in browser console output, test reports, screenshots, or CI artifacts.
5. Use the exact tenant ID required by each module. The AAA/NAS data uses a different database tenant ID from the Platform/BSS/Workforce/SIEM fixture.
6. On `401`, clear the session and show a login/session-expired state. On `403`, show an access-denied state. On `422`, render field-level validation details. Never show a generic “network error” for a valid API error response.

## CORS and local development

The gateway accepts browser origins from:

- `http://localhost:5173`
- `http://127.0.0.1:5173`
- `http://localhost:3000`
- `http://127.0.0.1:3000`
- `http://187.127.141.214:3001`

If the frontend uses another origin, add its exact scheme, host, and port to `infrastructure/gateway/nginx.conf` before testing. Include an automated browser preflight test for an authenticated BSS request.

## Gateway route contract

These are the public routes the frontend must use. Do not call Docker service ports directly.

| Module | Base route | Tenant rule |
| --- | --- | --- |
| Platform Auth | `/api/v1/auth` | JWT login and current-user only |
| Platform Users | `/api/v1/platform` | platform permission required |
| BSS | `/api/v1/bss` | send `tenant_id` query parameter or body field |
| OSS | `/api/v1/oss` | some resource routes require `tenant_id` query parameter |
| AAA legacy management | `/api/aaa` | use `tenant_id=E2E_AAA_TENANT_ID` |
| Canonical NAS | `/api/nas` | use `tenant_id=E2E_AAA_TENANT_ID` |
| NMS | `/api/v1/nms` | JWT tenant claim is used |
| IPAM | `/api/v1/ipam` | health currently available publicly |
| SIEM | `/api/v1/siem/v1` | JWT tenant claim is used |
| Workforce | `/api/v1/workforce/v1` | JWT tenant claim is used |
| Device Management | `/api/v1/device-management` | JWT tenant claim is used |
| Tenancy | `/api/v1/tenancy` | JWT tenant claim is used |
| Warehouse | `/api/v1/warehouse` | health route available |
| AIOps | `/api/v1/aiops` | status route available |
| Assurance | `/api/v1/assurance` | status route available |
| Intelligence | `/api/v1/intelligence` | status route available |

Note the intentionally doubled `v1` in the current public SIEM and Workforce routes. Treat these as the current contract until the gateway versioning is normalized.

## Plane structure

Create one Plane epic named **Milestone 10 Frontend API Validation**. Create the following tasks under it.

### P0 — Test foundation

- Create one typed API client with base URL, bearer-token interceptor, timeout, request ID, and JSON error parser.
- Add runtime configuration validation for all E2E variables.
- Add login, logout, token-expiry, and `GET /api/v1/auth/me` tests.
- Add a CORS preflight browser test for `/api/v1/bss/invoices`.
- Capture `auth-login.png`, `auth-session.png`, and `cors-preflight.png`.

### P0 — Read-only smoke suite

Each test must expect `200`, a valid JSON shape, and a loading/empty/error UI state.

```text
GET /api/v1/bss/invoices?tenant_id={platformTenantId}
GET /api/v1/bss/payments?tenant_id={platformTenantId}
GET /api/v1/bss/billing-accounts?tenant_id={platformTenantId}
GET /api/v1/bss/catalog/services?tenant_id={platformTenantId}
GET /api/v1/oss/orders
GET /api/v1/oss/subscriptions
GET /api/v1/oss/assets?tenant_id={platformTenantId}
GET /api/v1/oss/vendors?tenant_id={platformTenantId}
GET /api/nas?tenant_id={aaaTenantId}
GET /api/aaa/sessions?tenant_id={aaaTenantId}
GET /api/aaa/ip-pools?tenant_id={aaaTenantId}
GET /api/v1/nms/ops/escalation-policies
GET /api/v1/nms/ops/runbooks
GET /api/v1/siem/v1/security-events
GET /api/v1/siem/v1/policies
GET /api/v1/siem/v1/cases
GET /api/v1/siem/v1/dashboard/summary
GET /api/v1/workforce/v1/technicians
GET /api/v1/workforce/v1/work-orders
GET /api/v1/workforce/v1/dashboard/summary
GET /api/v1/device-management/devices
GET /api/v1/device-management/acs/instances
GET /api/v1/device-management/reports/overview
GET /api/v1/tenancy/tenants
GET /api/v1/ipam/health
GET /api/v1/warehouse/health
GET /api/v1/aiops/status
GET /api/v1/assurance/status
GET /api/v1/intelligence/status
```

Capture one overview image per screen/module, not a screenshot for every raw HTTP request.

### P1 — Safe create-and-read workflows

Use a unique run prefix such as `AUTOMATION-<UTC timestamp>-`. All created names must be generic and must be retrievable by the end of the same test. Persist created IDs in test context for follow-up reads; do not rely on ordering.

| Module | Safe workflow | Expected result |
| --- | --- | --- |
| BSS | `POST /api/v1/bss/catalog/services`, then list catalog services with `tenant_id` | `201`, then item appears with `ACTIVE` status |
| Workforce | `POST /api/v1/workforce/v1/technicians`, then list technicians | `201`, then technician appears as `AVAILABLE` |
| Workforce | `POST /api/v1/workforce/v1/work-orders`, then list work orders | `201`, then work order appears as `CREATED` |
| NMS | `POST /api/v1/nms/ops/escalation-policies`, then list policies | `200`, then policy is enabled |
| SIEM | `POST /api/v1/siem/v1/security-events`, then filter/list events | `201`, then event appears in tenant scope |

Required visual proof:

- Form validation screenshot before submission.
- Success toast and new table/card row after creation.
- Detail/drawer view for the newly created record.
- Empty state and API-error state for at least one list screen.

### P1 — Role and tenant isolation suite

- Use the tenant-scoped operator for normal positive tests.
- Use a platform-wide operator with `tenant_id: null` only to verify tenant-scoped screens deny access gracefully; do not expect tenant data to load.
- Attempt a request with a different tenant ID using a non-super-admin tenant token. Expect `403` and verify the UI does not show data from the other tenant.
- Verify tenant ID is never editable in normal end-user forms unless the feature is explicitly a platform-administration feature.

### P2 — Write operations requiring staging approval

Do not automate these against the shared VPS until a disposable staging environment and cleanup procedure exist:

- NAS connection test, discovery, credentials, credential rotation, RADIUS configuration, plan/apply/rollback, enable/disable, and decommission.
- Subscriber disconnect/CoA, session replay, IP lease release, and RADIUS secret changes.
- Payments, payment capture, refunds, chargebacks, manual-payment posting, dunning execution, and ledger rebuilds.
- Device discovery against a real ACS, configuration execution, firmware deployment, reboot/reset, and decommission.
- External notifications, webhook delivery, production reports, data erasure, security retention jobs, and legal-intercept operations.
- Any `DELETE` endpoint.

For each such endpoint, create a separate Plane task containing: target environment, fixture setup, expected external side effect, recovery/rollback step, reviewer, and evidence artifact.

## Test implementation requirements

- Use Playwright or Cypress for browser E2E tests; use an API test helper for setup and assertions.
- Use stable selectors such as `data-testid`; do not use brittle text-position selectors.
- Generate a unique test run ID once per run and include it in every generic fixture name, where the API schema permits it.
- Make POST tests idempotent where possible. If a unique-code conflict occurs, generate a new timestamped code rather than overwriting an existing record.
- Preserve created records on the shared VPS unless a reviewed cleanup endpoint is safe. Report their IDs in the test artifact.
- Store screenshots in `test-results/screenshots/` and attach the final report to the matching Plane task.
- Include request method, path, HTTP status, correlation/request ID when available, record ID, and screenshot path in the report. Never include secrets.

## Completion criteria

The epic is complete only when:

1. Authentication and CORS tests pass in a real browser.
2. Every P0 smoke endpoint passes and has an intentional frontend UI state.
3. Every P1 safe workflow proves create plus read-back using an ID generated during that run.
4. Tenant-isolation and unauthenticated/expired-session behavior are tested.
5. Screenshots and an API status report are attached to Plane tasks.
6. P2 endpoints are explicitly marked as deferred to staging, rather than silently untested.
