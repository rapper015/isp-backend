# Milestone 1 — CRM Service: Customer Lifecycle API

Service: `crm-service`. Auth: `X-CRM-Service-Key` (internal service key) with
management JWT fallback; all `/api/crm/*` routes are tenant-scoped.

## Health / status

| Method | Path | Description |
| --- | --- | --- |
| GET | `/health` | Liveness probe |
| GET | `/status` | Service phase/status |

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

## Milestone-0 compatibility routes

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
