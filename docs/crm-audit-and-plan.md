# CRM Microservice — Milestone 1 Audit and Migration Plan

## 1. Existing-code audit summary

The CRM bounded context lives in `services/crm-service` with two layers:

* **`app/` (FastAPI service)** — a minimal, production-facing microservice:
  * `models.py` — SQLAlchemy `Customer`, `Franchise`, `Branch`, `Lead`,
    `KycDocument`, `CustomerLifecycleEvent` (the Milestone 0 minimal mapping).
  * `main.py` — basic CRUD endpoints. **No tenant isolation, no RBAC, no
    events, no state machines, no audit, no Redis.**
  * `database.py` — SQLAlchemy engine (`crm.db` / `crm` database).

* **`legacy/` (Django apps, kept for the old core-platform)** — richer domain:
  * `customers` — `Franchise`, `Branch`, `Area`, `Customer` (status, national_id,
    caf_number, gstin, billing/installation addresses, lat/lng, document
    availability flags, import_metadata) + `CustomerCodeSequence`.
  * `leads` — `Lead` (priority, connection_type, lead_source, lead_type, gender,
    status, `assigned_to`, `is_callback`, `callback_at`).
  * `kyc` — `KycDocument` (document_type, file, expiry_date, status, verified_by).
  * `lifecycle` — `CustomerLifecycleEvent` and `transitions.py` (an explicit
    `ALLOWED_TRANSITIONS` map and a `has_verified_kyc` activation guard).
  * `resellers` — `Franchise`, `Branch` (franchise_code / branch_code).
  * `customers.franchises.py` — bridge between public reseller franchises and the
    legacy tenant franchise records.

## 2. Component classification

| Component | Decision | Rationale |
| --- | --- | --- |
| `app.models.Customer` (customer_code, full_name, phone, email, status) | **KEEP** | Milestone 0 contract; must remain API-compatible. |
| `app.models.Franchise/Branch/Lead/KycDocument/CustomerLifecycleEvent` | **EXTEND → MIGRATE** | Concepts are valid; they move into authoritative SQLAlchemy models in a `models/` package. |
| Legacy Django `customers`, `leads`, `kyc`, `lifecycle`, `resellers` | **MIGRATE (data) / KEEP (code)** | Preserve the apps until core-platform retires; new CRM service becomes authoritative. |
| Legacy `transitions.py` lifecycle map | **REFACTOR** | Replaced by a richer explicit state machine with events/audit. |
| `app/main.py` monolithic endpoints | **REFACTOR** | Split into services/selectors/models; add tenancy, RBAC, events, audit, idempotency. |
| No existing field/endpoint/migration removed | — | Nothing is dropped; all legacy data paths remain until verified backfill. |

## 3. Architecture decisions

* Keep the FastAPI + SQLAlchemy + Alembic conventions established by the AAA
  service; no new framework or ORM.
* **Tenant isolation**: every CRM-owned record is tenant-scoped. Tenant comes from
  trusted context (service key/JWT), never from the body.
* **Domain-driven folders**: `models/`, `services/`, `selectors/`, `events/`,
  `api/` — no giant `models.py`/`views.py`.
* **Explicit state machines** for leads, customer lifecycle, KYC and CAF — direct
  status PATCHing is not allowed.
* **Transactional outbox** for RabbitMQ events + consumer inbox deduplication.
* **Redis** for caches, rate limits, duplicate/conversion/merge locks, follow-up
  scheduling — never authoritative.
* **Boundaries**: CRM owns leads, customer identity/profile, contacts, addresses,
  service-location identity, CAF, KYC, lifecycle, risk, ownership, timeline,
  audit. BSS/OSS/AAA/IPAM/NMS data is stored only as external references / read
  projections.

## 4. Milestone 1 scope (implemented)

1. Lead pipeline (sources, stages, assignment, interactions, follow-ups,
   qualification, conversion).
2. KYC + identity verification (cases, documents, secure access).
3. Customer profile + service address (contacts, structured versioned addresses,
   CAF records).
4. Lifecycle + risk (state machine, explainable risk aggregation, timeline).

## 5. Migration strategy (data)

Staged: (1) add new schema; (2) backfill normalized records from legacy/minimal
mappings; (3) validate counts; (4) compatibility-adapt reads; (5) switch reads;
(6) switch writes; (7) deprecate obsolete fields. Old migration files are never
rewritten. Reconciliation scripts are provided.
