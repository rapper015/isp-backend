# Milestone 1 CRM audit against the master implementation specification

Audit date: 2026-09-22  
Branch inspected: `milestone-10`  
Owning service: `services/crm-service`

## Executive result

Milestone 1 is **operational for its original delivery slice**, but it is **not
complete against `TELECOM_ISP_BACKEND_MASTER_IMPLEMENTATION_SPEC.md`**.

The original milestone covers lead capture/conversion, customer profiles,
contacts and addresses, KYC/CAF, lifecycle/risk, follow-ups, franchises and
branches. Those APIs are running locally, the CRM schema is at Alembic head,
and the correctly configured service test suite passes.

The master specification assigns 141 feature records to `crm-service`. The
current generated coverage evidence classifies them as:

| Status | Count |
|---|---:|
| COMPLETE | 17 |
| PARTIAL | 120 |
| BLOCKED_EXTERNAL | 4 |
| Total | 141 |

Priority distribution of unresolved CRM features:

| Priority/status | Count |
|---|---:|
| P0 PARTIAL | 87 |
| P0 BLOCKED_EXTERNAL | 4 |
| P1 PARTIAL | 32 |
| P2 PARTIAL | 1 |

The milestone must therefore not be presented as master-spec complete.

## Verification performed

- Inspected the tracked CRM service, models, services, migrations, security,
  event publisher, worker code, gateway routes, API handoff, existing audit,
  and generated 1,500-feature coverage matrix.
- Verified PostgreSQL migration state: `0004_branch_profile (head)`.
- Ran the CRM suite with its documented test service credential: **33 passed**.
- Verified gateway authentication and successful HTTP 200 reads for tenants,
  leads, customers, follow-ups, franchises and branches.
- Verified `/api/crm/audit` cannot currently perform the new unfiltered
  platform-admin read; it returns 422 without `tenant_id`.
- Preserved the dirty working tree and made no database-destructive changes.

The first container test run failed because the running container's deployment
key differs from the test suite's hard-coded key. Supplying the intended test
key produced 33 passing tests. This should be repaired in the test fixture or
test Compose profile so the standard test command is hermetic.

## Confirmed complete feature evidence

The current evidence marks these feature IDs complete:

`51, 55, 61, 67, 71, 72, 76, 88, 312, 392, 821, 826, 1190, 1191, 1323, 1459, 1460`.

They cover core lead/customer/KYC behavior plus selected escalation, partner,
knowledge-feedback, recovery and loyalty behavior. Completion here means the
repository has matching API/model/test evidence; it does not make adjacent
feature families complete.

## Major functional gaps

### Lead and opportunity management (features 52-65)

Implemented: lead capture, duplicate warnings, assignment, stage transitions,
interactions, follow-ups, feasibility and conversion.

Missing or incomplete: general lead update/delete, bulk reseller ingestion,
configurable scoring, a dedicated notes contract, opportunity aggregate,
proposal generation and win/loss workflow. Duplicate detection exists but does
not yet satisfy the full configurable prevention/review requirement.

### Customer, portal and retention (features 66-100)

Implemented: customer create/update/merge, 360 read, timeline, contacts,
versioned addresses, lifecycle, risk, KYC/CAF and basic interactions.

Missing or incomplete: segmentation and tags, relationship mapping,
subscriber-scoped portal identity and ownership enforcement, self-service
profile/service-request/plan-change/payment/complaint contracts, usage read
model, churn prediction/campaigns, feedback/NPS, customer archive/retrieval,
retention policies, workflow/rule engine, notification orchestration and an
external CRM adapter.

The eKYC and external CRM items should only remain `BLOCKED_EXTERNAL` after a
secure adapter contract, mock implementation, timeout/retry behavior and tests
exist. Provider credentials alone are not enough to justify that status.

### Ticketing, SLA and incident management (features 301-328)

The service currently stores ticket SLA timers, escalations and suggestions,
but it has no authoritative Ticket aggregate or complete ticket CRUD/workflow.
Creation, update, assignment, priority/category, comments, attachments,
merge/split, closure validation, incidents, war rooms, incident timelines,
post-incident review, service-request catalog and approvals are incomplete.

This is the largest P0 functional gap. Existing SLA endpoints must not be
described as a complete ticketing system.

### Reseller and partner management (features 351-400, 821-829)

Implemented: franchise/branch profiles and settings, generic partner records,
performance snapshots, hierarchy links, SLA evaluation and federation links.

Missing or incomplete: reseller lifecycle/deactivation, depth and cycle
constraints, territories, reseller-auth ownership scope, dashboards/read
models, customer/lead/ticket delegated access, provisioning requests, plan
assignment, suspension controls, branding/domain/app configuration, feature
entitlements, reseller API credentials/webhooks, reseller-specific audit and
access monitoring, scalable hierarchy traversal and several federation flows.

### Advanced CRM/CX features

Most CRM-owned features in the 800-1500 range remain partial. Current
implementations for KB feedback, suggested resolutions, experience recovery
and loyalty scoring are narrow foundations. QoE, journey analytics,
interventions, renewals, smart routing, onboarding journeys/SLA,
cross-channel continuity and session-to-journey mapping are not complete.

## Production-standard gaps

These gaps apply even to parts that are functionally present:

1. The event envelope omits master-spec fields including `aggregate_type`,
   `aggregate_id`, `actor_type`, `actor_id` and `trace_id`; `causation_id` is
   always null and correlation headers are not consistently propagated.
2. Several ecosystem endpoints accept raw `dict` payloads instead of versioned
   Pydantic contracts with bounded validation.
3. Fine-grained JWT permissions exist, but tests do not prove the permission
   matrix, tenant-bound JWT isolation, resource ownership or subscriber self-
   service scope. Trusted service keys bypass user RBAC by design and require a
   documented caller policy.
4. Platform-wide list reads were added for wildcard administrators, but the
   master spec requires explicit cross-tenant permission, justification and an
   immutable audit entry. Directory reads and audit search are inconsistent.
5. Command idempotency is not consistently exposed through `Idempotency-Key`;
   optimistic concurrency/version preconditions are absent for mutable
   resources.
6. The outbox/inbox foundation exists, but there is insufficient evidence for
   bounded retry with jitter, poison-message dead-letter progression,
   replay-safe consumers and failure recovery.
7. Observability is limited: no CRM-specific metrics/tracing evidence or SLO
   tests were found for the milestone.
8. Tests do not cover migration upgrade/backfill, PostgreSQL concurrency,
   RabbitMQ retry/DLQ behavior, Redis failure, external adapter failures,
   gateway contract compatibility or load limits.
9. `main.py` remains a large route module. This is maintainable for the current
   slice but should be split by bounded domain before adding the missing
   ticketing, portal and automation families.
10. Legacy compatibility code remains tracked. The master specification allows
    it only as migration compatibility; authoritative behavior must stay in
    `services/crm-service/app` and needs a documented retirement checkpoint.

## Recommended completion order

1. **Foundation hardening:** hermetic test profile, complete event envelope,
   correlation propagation, permission/tenant test matrix, idempotency helper,
   cross-tenant read audit and validated request schemas.
2. **Close original M1 gaps:** lead update/delete/notes/scoring, segmentation,
   tags, relationships, KYC audit and configurable duplicate adjudication.
3. **Ticketing/ITSM P0:** Ticket and SLA policy aggregates, migrations, API,
   validated state machines, comments, assignment, closure, incident flow,
   outbox events and failure tests.
4. **Subscriber self-service contracts:** subscriber principal/ownership scope
   and event-driven requests to BSS/OSS rather than duplicating their state.
5. **Reseller completion:** hierarchy invariants, territories, delegated scope,
   entitlements, branding, credentials/webhooks and audit/read models.
6. **Retention and automation:** archive/retention, workflows/rules,
   notifications, churn/feedback/NPS and campaign orchestration.
7. **External adapters and advanced CX:** implement mockable contracts first,
   then mark production dependencies `BLOCKED_EXTERNAL` only where credentials
   or provider approval are the remaining blocker.
8. Re-run service, contract, tenant-isolation, migration, failure and gateway
   tests; update `client-feature-coverage.json` only from explicit acceptance
   evidence.

## Definition-of-done decision

Current status:

- **Original Milestone 1 API slice:** implemented and locally operational,
  with production-hardening work remaining.
- **Milestone 1 against the master implementation specification:** **NOT
  COMPLETE**.
- **Safe next implementation batch:** foundation hardening followed by the P0
  ticketing/ITSM aggregate and the remaining original-M1 P0 gaps.

