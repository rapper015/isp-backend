# CRM Service — Milestone 1 (Customer Lifecycle Management)

This document describes the CRM bounded context implementation on the
`milestone-1` branch. It extends the Milestone 0 minimal customer mapping
(`customer_code`, `full_name`, `phone`, `email`, `status`) into the
authoritative CRM domain for customer identity, profile, contacts, addresses,
service locations, CAF, KYC, lifecycle and risk.

## Architecture

The CRM service (`services/crm-service`) follows the same conventions as the
AAA service: FastAPI + SQLAlchemy + Alembic + transactional outbox + Redis.
Folder structure:

```
app/
    database.py          engine / session (own crm database)
    enums.py             canonical enums (no duplicates in models/views)
    state_machine.py     validated lead / lifecycle / KYC / CAF machines
    validation.py        phone/email/zipcode/coordinates + masking
    cache.py             Redis helpers (caches, rate limits, locks)
    events.py            outbox publishing + inbox-deduplicated consumers
    security.py          RBAC permissions + internal/JWT auth + encryption
    schemas.py           pydantic request schemas
    main.py              /api/crm/* routes (+ Milestone 0 legacy routes)
    models/              tenant, lead, customer, kyc, caf, lifecycle, audit
    services/            lead, conversion, customer, kyc, caf, lifecycle,
                         risk, duplicate, merge, followup, timeline, 360, audit
migrations/versions/0001_crm_baseline.py
tests/                  hermetic suite (17 tests)
```

## Ownership boundaries enforced

* **CRM owns**: leads, customer identity/profile, contacts, addresses,
  service-location identity, CAF, KYC cases/documents, lifecycle, risk,
  ownership, timeline, audit.
* **BSS/OSS/AAA/IPAM/NMS/Workforce data lives elsewhere**; CRM stores only
  external references / read projections (`crm_external_references`) and never
  becomes the financial, network or authentication source of truth. No
  plaintext AAA passwords, no invoice balances, no IP allocations in CRM.

## Models (app/models)

* `Tenant` — top-level isolation boundary.
* `Lead`, `LeadAssignment`, `LeadInteraction`, `FollowUp`, `LeadStageHistory`.
* `Franchise`, `Branch`, `Customer` (preserves Milestone 0 `customer_code`),
  `Contact`, `Address` (versioned), `ServiceLocation`, `CustomerOwnership`,
  `ExternalReference`.
* `KycCase`, `KycDocument` (masked identifiers + private storage references).
* `CafRecord`.
* `CustomerLifecycleEvent`, `CustomerRisk`, `TimelineEntry` (immutable).
* `AuditLog`, `OutboxEvent`, `ConsumerInbox` (outbox/inbox), plus
  `crm_customer_aliases` for merge redirects.

## State machines

All transitions are validated; direct status patching is not allowed
(`app/state_machine.py`):

* **Lead pipeline**: `NEW → ASSIGNED → CONTACTED → QUALIFICATION →
  FEASIBILITY_PENDING → FEASIBLE → PROPOSAL_SENT → NEGOTIATION → WON →
  CONVERTED` with `LOST`/`DISQUALIFIED`/`DUPLICATE` and reopen support.
* **Customer lifecycle**: `PROSPECT → ONBOARDING → KYC_PENDING → KYC_VERIFIED
  → READY_FOR_SERVICE → ACTIVATION_PENDING → ACTIVE` with suspension,
  reactivation, termination and `CLOSED` terminal state.
* **KYC**: `DRAFT → SUBMITTED → UNDER_REVIEW → VERIFIED/REJECTED` with
  `ADDITIONAL_INFO_REQUIRED`, `EXPIRED`, `REVOKED`.
* **CAF**: `DRAFT → SUBMITTED → UNDER_REVIEW → VERIFIED → APPROVED` with
  `INCOMPLETE`, `REJECTED`, `CANCELLED`, `SUPERSEDED`.

Every lifecycle/KYC/CAF/lead transition records audit, timeline and a versioned
event.

## APIs

All under `/api/crm/*`, tenant-scoped, permission-checked, audited:

* **Leads**: `POST/GET /api/crm/leads`, `GET/PATCH /{id}`,
  `/{id}/assign`, `/{id}/transition`, `/{id}/qualify`,
  `/{id}/request-feasibility`, `/{id}/feasibility-result`,
  `/{id}/interactions`, `/{id}/follow-ups`, `/{id}/convert`, `/{id}/reopen`,
  `/{id}/history`.
* **Interactions/follow-ups**: customer interactions/follow-ups,
  `GET /api/crm/follow-ups`, `/{id}/complete`, `/{id}/reschedule`.
* **Customers**: CRUD, `/{id}/360`, `/{id}/timeline`, `/{id}/transition`,
  `/{id}/merge-preview`, `/{id}/merge`, `/{id}/external-references`,
  `/{id}/risk`, `/{id}/risk/override`, `/{id}/audit`.
* **Contacts/addresses**: `/{id}/contacts` (+verify), `/{id}/addresses`
  (+history), `/{id}/service-locations`.
* **KYC**: `/{id}/kyc`, `/api/crm/kyc/{id}/submit|request-information|verify|
  reject`, `/{id}/documents`.
* **CAF**: `/{id}/caf`, `/api/crm/caf/{id}/submit|approve|reject`.
* **Other**: `/api/crm/tenants`, `/franchises`, `/branches`, `/duplicates`,
  `/audit`.
* **Milestone 0 legacy routes preserved**: `/customers`,
  `/customers/by-code/{code}`, `/franchises`, `/branches`, `/leads`,
  `/customers/{id}/lifecycle-events`, `/customers/{id}/kyc-documents`.

## Events (RabbitMQ)

Published (via transactional outbox): `crm.lead.created/assigned/stage_changed/
feasibility_requested/converted.v1`, `crm.customer.created/updated/merged/
contact_verified/address_changed/lifecycle_changed/risk_changed.v1`,
`crm.kyc.submitted/verified/rejected.v1`, `crm.caf.approved.v1`,
`crm.followup.due/overdue.v1`, plus downstream requests
`crm.billing_account.requested.v1` and `crm.service_order.requested.v1`.
Consumers use an inbox for idempotency. No documents, identity numbers or
passwords are ever published.

## Redis

Keys under `crm:v1:` — customer-360 cache (`{tenant}:customer360:{id}`), rate
limits, duplicate/conversion/merge locks, follow-up scheduling. Never
authoritative; database constraints are the final protection.

## Security and privacy

* Fernet encryption for sensitive values; identity numbers stored masked.
* Private document storage references only — no public media URLs, no binaries
  in events/logs.
* RBAC roles (`CRM_MANAGER`, `SALES_AGENT`, `KYC_REVIEWER`, …) with granular
  permissions; sensitive document access requires `crm.document.view_sensitive`.
* Audit records carry safe before/after values; no plaintext identity numbers,
  full mobile numbers or credentials.

## Testing

`tests/` (17 tests, hermetic conftest): state machines, lead pipeline,
conversion idempotency, follow-ups, customer contacts/addresses (versioning),
lifecycle/risk, merge, tenant isolation, KYC masking, CAF, Milestone 0
backward compatibility, and a full end-to-end lead→customer lifecycle.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -r ..\..\shared\runtime\requirements.txt
.\.venv\Scripts\python -m pytest
```

## Migration strategy

`0001_crm_baseline` creates the authoritative schema. Legacy Django apps under
`services/crm-service/legacy/` are preserved for the old core-platform; the
FastAPI service is authoritative. Backfills from the old minimal `customers`
table and legacy fields are staged (add schema → backfill → validate → switch
reads → switch writes → deprecate) and must be verified per environment.
