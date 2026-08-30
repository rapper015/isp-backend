# ADR-010 — Client Master Spec: Dependency Resolution & Ownership Mapping

Status: **Accepted** · Date: 2026-08-30

## Context

The client master specification (`docs/TELECOM_ISP_BACKEND_MASTER_IMPLEMENTATION_SPEC.md`,
1,500 features) contains ambiguous dependency cells, a non-standard access code,
an unexplained extra column, non-unique source event names, and recommended
owners that do not exactly match the repository's tracked service names. Per
the spec (sections 4–5), these must be resolved through documented decisions,
not silent guesses.

## Decisions

### D1 — Ambiguous dependency cells

| Feature | Cell value | Resolution |
|---|---|---|
| 117 (Bill Generation) | `109113` | Treat as `[109, 113]` (Plan Assigned, Offline Charging). |
| 1011 (Service-Resource Mapping) | `10091010` | Treat as `[1009, 1010]` (Service Catalog, Resource Catalog). |

### D2 — Forward references
Features 1086 (→ `1142`), 1089 (→ `1193`) and 1100 (→ `1193`) reference later
feature IDs. These are **forward dependency edges** and are preserved as-is;
they do not affect implementation ordering because we build by owner + priority.

### D3 — Qualified references
Dependency cells such as `CRM-71`, `BSS-137`, `OSS-201`, `NMS-271`, `Core-14`,
`SLA-310`, `AAA-162`, `IPAM-216` are normalized to the corresponding numeric
feature ids per the module ID ranges in the workbook module inventory
(CRM=51–100, BSS=101–150, AAA=151–200, OSS=201–250, NMS=251–300, SLA/ITSM=301–328,
Core=1–50, IPAM=216–223, etc.). The domain prefix is retained in structured
dependency records for traceability.

### D4 — Feature 1405 access code
Feature 1405 (Onboarding Journey Tracking) declares access code `CRM`, which is
not present in the AccessLevel sheet. Resolved to **CSR** (Customer Support) as
the likely intended role; recorded, not silently replaced.

### D5 — Feature 22 "YTD" extra column
An unnamed thirteenth column on feature 22 (SSO Integration) contains `YTD`.
Treated as **source metadata** and ignored for implementation. Recorded here
pending clarification.

### D6 — Event name normalization
Source events (e.g. `TenantCreated`, `LeadCreated`) are not globally unique and
are not versioned. Converted to namespaced versioned contracts of the form
`<bounded-context>.<aggregate>.<past-tense-action>.v1`
(e.g. `tenancy.tenant.created.v1`, `crm.lead.created.v1`). Each normalized
contract retains the source event as an alias in the coverage record
(`source_event`). No existing consumers are broken; deprecated aliases are
documented.

### D7 — Recommended-owner → tracked-service mapping
The workbook recommends `core-platform-service`, `data-warehouse-service`, etc.,
but the repository uses different tracked names. Mapping (recorded so coverage
evidence is attributed to the real code):

| Recommended owner | Tracked evidence service(s) |
|---|---|
| core-platform-service | `tenancy-service` (tenant lifecycle, IAM/RBAC, config/branding, partners, commissions, settlements) |
| crm-service | `crm-service`, `support-service` (support/ticketing rows) |
| bss-service | `bss-service` |
| oss-service | `oss-service` |
| aaa-service | `aaa-service` |
| nms-service | `nms-service`, `assurance-service` (alerts/incidents/SLO/monitoring) |
| ipam-service | `ipam-service`, `aaa-service` (IP pool/lease models live in aaa) |
| siem-service | `siem-service` |
| workforce-service | `workforce-service` |
| data-warehouse-service | `warehouse-service`, `intelligence-service` (governed ingestion/datasets/analytics) |
| aiops-service | `intelligence-service` (M10 AI layer; `aiops-service` skeleton is deprecated) |

### D8 — Language discrepancy (spec §4)
The workbook "Architecture" sheet states "Backend: Rust Microservices". The
tracked repository is **Python/FastAPI**. Per the spec's architecture-conflict
rule, the repository is authoritative for language/framework; **no Rust
migration is performed**. This ADR records the discrepancy; a separate explicit
architecture decision is required before any language migration.

## Consequences

- Coverage documents attribute evidence to tracked services via D7.
- Dependency graph is normalized (D1–D3) for implementation order.
- Statuses in `docs/client-feature-coverage.json` are conservative (evidence-based).
- No consumer breakage from event normalization (D6).
