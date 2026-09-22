# Milestone 1 — Complete Frontend API Handoff

This is the **only Milestone 1 file a frontend developer or coding agent needs**.
It combines authentication, setup order, screen workflows, request examples,
and the complete CRM endpoint inventory.

Service: `crm-service`. All `/api/crm/*` routes are tenant-scoped. Frontend
clients authenticate with a Platform Core bearer token. `X-CRM-Service-Key` is
strictly for trusted backend-to-backend traffic and must never be shipped in a
browser application.

## Access through the M1 Docker gateway

Use `https://api.example.com` (local Docker: `http://localhost:4000`) and
`Authorization: Bearer <access_token>`. Login/refresh/logout/me use the M0
`/api/v1/auth/*` routes. Never expose CRM internal keys. The backend
`CORS_ALLOWED_ORIGINS` must contain the exact frontend scheme, host, and port.

## Frontend setup and screen map

Create and retain IDs in this order: tenant → franchise → branch → lead or
customer. Pass `tenant_id` exactly where the endpoint requires it.

| Screen | Reads/actions | Required behaviour |
| --- | --- | --- |
| Leads | list/detail/history, assign, transition, qualify, feasibility, convert | Reload after transitions; preserve conversion idempotency key. |
| Follow-ups | list, complete, reschedule | Use backend due/status and disable duplicate submits. |
| Customer 360 | detail, 360, timeline, update, transition | Load sections independently; backend lifecycle is authoritative. |
| Contacts/addresses | create/update/verify/history | Keep verification and address-version history visible. |
| KYC/CAF | cases/documents, submit, request info, verify/approve/reject | Show controls only for current state and permission. |
| Risk/merge | assess/override, duplicates, merge preview/merge | Always preview and confirmation-gate merge/override. |
| Audit | audit/timeline | Read-only; show actor, time, and correlation ID. |

Example lead body:

```json
{"first_name":"Asha","last_name":"Sharma","primary_mobile":"9876543210","primary_email":"asha@example.com","lead_source":"WEBSITE","branch_id":"<branch-uuid>","installation_address_draft":{"city":"Pune","state":"Maharashtra"}}
```

On 401 refresh once; on 403 show access denied; on 409 refresh state; map 422
detail to the form; back off on 429; retain form inputs on 5xx.

Use `http://<host>:4000/api/crm/*` for the CRM APIs in this document. The
gateway also accepts `/api/v1/crm/*` as a compatibility prefix and forwards it
to the same CRM routes. `crm-service` is not published directly.

Authenticate a human operator with Platform Core first:

```http
POST /api/v1/auth/login
Content-Type: application/json

{"username":"admin","password":"<PLATFORM_BOOTSTRAP_ADMIN_PASSWORD>"}
```

Send the returned `access_token` as `Authorization: Bearer <token>`. The
gateway does not publish the CRM health endpoint. The frontend workflow and
request examples are included above in this same file.

Except for tenant creation, CRM requires `tenant_id` as a **query parameter**,
including create and mutation requests. Do not put `tenant_id` inside CRM JSON
bodies unless a future endpoint explicitly documents it.

## Health / status

| Method | Path | Description |
| --- | --- | --- |
| GET | `/health` | Liveness probe; service-internal, not gateway-proxied |
| GET | `/status` | Service phase/status; service-internal, not gateway-proxied |

## Tenants, franchises, branches

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/crm/tenants` | Create tenant |
| POST | `/api/crm/franchises` | Create franchise |
| POST | `/api/crm/branches` | Create branch |

## Leads

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/crm/leads` | Capture a lead (idempotent, duplicate detection) |
| GET | `/api/crm/leads` | List leads |
| GET | `/api/crm/leads/{lead_id}` | Lead detail |
| POST | `/api/crm/leads/{lead_id}/assign` | Assign lead to agent |
| POST | `/api/crm/leads/{lead_id}/transition` | Transition lead stage (validated) |
| POST | `/api/crm/leads/{lead_id}/qualify` | Qualify a lead |
| POST | `/api/crm/leads/{lead_id}/request-feasibility` | Request feasibility check |
| POST | `/api/crm/leads/{lead_id}/feasibility-result` | Record feasibility result |
| POST | `/api/crm/leads/{lead_id}/interactions` | Add lead interaction |
| POST | `/api/crm/leads/{lead_id}/follow-ups` | Schedule follow-up |
| POST | `/api/crm/leads/{lead_id}/convert` | Convert lead → customer (publishes events) |
| POST | `/api/crm/leads/{lead_id}/reopen` | Reopen a converted/lost lead |
| GET | `/api/crm/leads/{lead_id}/history` | Lead stage history |

## Follow-ups

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/crm/follow-ups` | List follow-ups |
| POST | `/api/crm/follow-ups/{followup_id}/complete` | Complete follow-up |
| POST | `/api/crm/follow-ups/{followup_id}/reschedule` | Reschedule follow-up |

## Customers

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/crm/customers` | Create customer (from lead or directly) |
| GET | `/api/crm/customers` | List customers |
| GET | `/api/crm/customers/{customer_id}` | Customer detail |
| PATCH | `/api/crm/customers/{customer_id}` | Update customer |
| GET | `/api/crm/customers/{customer_id}/360` | Customer 360 view |
| GET | `/api/crm/customers/{customer_id}/timeline` | Lifecycle timeline |
| POST | `/api/crm/customers/{customer_id}/transition` | Lifecycle transition (validated) |
| POST | `/api/crm/customers/{customer_id}/merge-preview` | Preview customer merge |
| POST | `/api/crm/customers/{customer_id}/merge` | Merge duplicate customers |
| GET | `/api/crm/customers/{customer_id}/external-references` | List external references |
| POST | `/api/crm/customers/{customer_id}/external-references` | Add external reference |
| GET | `/api/crm/duplicates` | Find duplicate customers |
| GET | `/api/crm/customers/{customer_id}/audit` | Customer audit trail |

## Contacts & addresses

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/crm/customers/{customer_id}/contacts` | Add contact |
| PATCH | `/api/crm/customers/{customer_id}/contacts/{contact_id}` | Update contact |
| POST | `/api/crm/customers/{customer_id}/contacts/{contact_id}/verify` | Verify contact |
| POST | `/api/crm/customers/{customer_id}/addresses` | Add address (versioned) |
| PATCH | `/api/crm/customers/{customer_id}/addresses/{address_id}` | Update address |
| GET | `/api/crm/customers/{customer_id}/addresses/history` | Address version history |
| POST | `/api/crm/customers/{customer_id}/service-locations` | Add service location |

## KYC

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/crm/customers/{customer_id}/kyc` | Create KYC case |
| GET | `/api/crm/customers/{customer_id}/kyc` | List KYC cases |
| POST | `/api/crm/kyc/{case_id}/submit` | Submit KYC for review |
| POST | `/api/crm/kyc/{case_id}/request-information` | Request more information |
| POST | `/api/crm/kyc/{case_id}/verify` | Verify KYC |
| POST | `/api/crm/kyc/{case_id}/reject` | Reject KYC |
| POST | `/api/crm/kyc/{case_id}/documents` | Attach document |
| GET | `/api/crm/kyc/{case_id}/documents` | List documents |

## CAF (Customer Application Form)

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/crm/customers/{customer_id}/caf` | Create CAF record |
| POST | `/api/crm/caf/{caf_id}/submit` | Submit CAF |
| POST | `/api/crm/caf/{caf_id}/approve` | Approve CAF |
| POST | `/api/crm/caf/{caf_id}/reject` | Reject CAF |

## Risk

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/crm/customers/{customer_id}/risk` | Assess/set customer risk |
| POST | `/api/crm/customers/{customer_id}/risk/override` | Override risk (audited) |
| GET | `/api/crm/customers/{customer_id}/risk` | Customer risk profile |

## Audit

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/crm/audit` | CRM audit log (tenant-scoped, filterable) |

## Service-internal legacy compatibility routes

These routes exist on `crm-service` but are not proxied by the M1 Docker
gateway. The gateway exposes only `/api/v1/customers*` compatibility routes.

| Method | Path | Description |
| --- | --- | --- |
| POST | `/customers` | Legacy create customer |
| GET | `/customers` | Legacy list customers |
| GET | `/customers/by-code/{customer_code}` | Legacy customer by code |
| GET | `/customers/{customer_id}` | Legacy customer detail |
| POST | `/customers/{customer_id}/lifecycle-events` | Legacy lifecycle event |
| GET | `/customers/{customer_id}/kyc-documents` | Legacy KYC documents |
| GET | `/leads` | Legacy list leads |
| POST | `/leads` | Legacy create lead |
| POST | `/franchises` | Legacy create franchise |
| POST | `/branches` | Legacy create branch |
