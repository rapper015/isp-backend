# Milestone 6 — Workforce: Field Operations Management

Service: `workforce-service` · API base (via gateway): `/api/v1/workforce/`
· internal base: `/api/workforce/`

This milestone provides a **single canonical field-work-order model** (separate
from OSS orders and support tickets), appointments, visits, check-in/out,
technician profiles, explainable assignment scoring, dispatch planning and
conflict detection, GPS geofenced check-in with governed exceptions, versioned
execution checklists, proof of work with private media, a QA workflow, field
SLA with calendars/pauses and at-risk/breach escalation, inventory integration
(reserve/issue/install/consume, one device on one service) and offline-first
mobile sync.

There is **no** `PATCH /work-orders/{id} {"status": ...}`. Every state change is
an explicit command endpoint validated by the work-order state machine.

## Authentication

| Surface | Scheme | Variable |
| --- | --- | --- |
| Management (admin/dispatch/QA/SLA) | JWT RBAC `Authorization: Bearer` | `WORKFORCE_JWT_SECRET` |
| Technician mobile | JWT (role `TECHNICIAN`) | `WORKFORCE_TECHNICIAN_JWT_SECRET` |
| Customer portal | JWT (role `CUSTOMER`/`PORTAL_USER`) | `WORKFORCE_CUSTOMER_JWT_SECRET` |
| Service-to-service | `X-Internal-API-Key` | `WORKFORCE_INTERNAL_API_KEY` |

RBAC roles include `PLATFORM_ADMIN`, `ISP_OWNER`, `ISP_ADMIN`,
`FIELD_SUPERVISOR`, `DISPATCHER`, `QA_REVIEWER`, `INVENTORY_CONTROLLER`,
`NOC_ENGINEER`, `SUPPORT_AGENT`, `OSS_OPERATOR`, `FRANCHISE_OPERATOR`,
`AUDITOR`, `READ_ONLY`. Tenant scope always comes from the authenticated
principal; a mismatched `tenant_id` query/body is rejected.

---

## Work orders (management)

All endpoints require management JWT and are tenant-scoped.

| Method | Path | Permission | Purpose |
| --- | --- | --- | --- |
| POST | `/work-orders` | `workforce.work.create` | Create a work order (creates template snapshot, checklist snapshot, field SLA, outbox `created` event). `idempotency_key` supported. |
| GET | `/work-orders` | `workforce.work.view` | List/search. Filters: `status`, `work_order_type`, `priority`, `technician_id`, `customer_id`, `service_location_id`, `oss_order_id`, `support_ticket_id`, `sla_status`, `search`. |
| GET | `/work-orders/{id}` | `workforce.work.view` | Full detail: snapshot, appointments, events, proof summary. |
| GET | `/work-orders/{id}/events` | `workforce.work.view` | Immutable event history. |
| GET | `/work-orders/{id}/valid-actions` | `workforce.work.view` | Allowed transitions from the current state. |
| GET | `/work-orders/{id}/sla` | `workforce.work.view` | Field SLA timeline (status, deadlines, pauses). |
| POST | `/work-orders/{id}/validate` | `workforce.work.view` | `CREATED → READY_FOR_SCHEDULING`. |
| POST | `/work-orders/{id}/schedule` | `workforce.work.schedule` | Create appointment (window) → `SCHEDULED`; appointment enters `CUSTOMER_CONFIRMATION_PENDING`. |
| POST | `/work-orders/{id}/reschedule` | `workforce.work.schedule` | Reschedule appointment → `RESCHEDULE_REQUIRED`. |
| POST | `/work-orders/{id}/assign` | `workforce.work.assign` | Assign technician (manual with `reason`, or `strategy`). Persists explainable score breakdown. |
| POST | `/work-orders/{id}/reassign` | `workforce.work.assign` | Reassign (requires `reason`). |
| POST | `/work-orders/{id}/dispatch` | `workforce.work.dispatch` | Dispatch assigned technician → `DISPATCHED`. |
| POST | `/work-orders/{id}/cancel` | `workforce.work.cancel` | Cancel (requires `reason`). |
| POST | `/work-orders/{id}/fail` | `workforce.work.view` | Fail (requires `reason`). |
| POST | `/work-orders/{id}/complete` | `workforce.work.complete` | Complete with `result_code` + summary; blocked before QA approval when QA is required. |
| POST | `/work-orders/{id}/link-order` | `workforce.work.view` | Link an OSS order. |
| POST | `/work-orders/{id}/link-ticket` | `workforce.work.view` | Link a support ticket. |
| POST | `/work-orders/{id}/link-incident` | `workforce.work.view` | Link an NMS incident. |
| POST | `/work-orders/{id}/related` | `workforce.work.view` | Link a related work order. |
| POST | `/work-orders/{id}/sla/exception` | `workforce.sla.manage` | Supervisor-approved SLA deadline exception. |

### Example — create

```json
POST /api/v1/workforce/work-orders
{
  "tenant_id": "…",
  "work_order_type": "NEW_INSTALLATION",
  "customer_id": "CUST-0001",
  "customer_name": "Test Customer",
  "service_subscription_id": "SUB-0001",
  "service_location_id": "loc-1",
  "latitude": 28.6139,
  "longitude": 77.2090,
  "priority": "P3_MEDIUM",
  "source_channel": "API"
}
```

## Dispatch

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/dispatch/unassigned` | Work orders awaiting assignment. |
| GET | `/dispatch/board` | Per-technician daily load (`?date=YYYY-MM-DD`). |
| GET | `/dispatch/recommendations/{work_order_id}` | Scored technician recommendations. |
| POST | `/dispatch/validate-assignment` | Validate a proposed assignment (skills, certs, availability, conflicts). |
| POST | `/dispatch/bulk-preview` | Preview recommended technicians for many work orders. |
| GET | `/dispatch/plans/{technician_id}` | Dispatch plan (`?date=`). |
| POST | `/dispatch/plans/{technician_id}/sequence` | Optimistic-concurrency plan re-sequence. |
| GET | `/dispatch/plans/{technician_id}/route` | Nearest-neighbour route with travel buffers. |

## Technician profiles (management)

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/technicians` | Create technician profile. |
| GET | `/technicians` | List (`?active=`). |
| POST | `/technicians/{id}/skills` | Add/update skill + proficiency. |
| POST | `/technicians/{id}/certifications` | Add certification with expiry. |
| POST | `/technicians/{id}/availability` | Daily availability. |
| POST | `/technicians/{id}/shifts` | Recurring shift (0=Mon..6=Sun). |
| POST | `/technicians/{id}/status` | Operational status transition (AVAILABLE, RESERVED, DISPATCHED, EN_ROUTE, WORKING, OFF_SHIFT, UNAVAILABLE, …). |
| POST | `/technicians/{id}/certification-exceptions` | Supervisor-approved exception for an expired certification. |

## Technician mobile

Requires technician JWT. The technician identity (id + tenant) comes from the
token; a technician can only operate on work orders assigned to them.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/technician/me` | Current technician identity. |
| GET | `/technician/assignments` | My open assignments (privacy-safe customer view). |
| GET | `/technician/assignments/{id}` | Assignment detail incl. checklist snapshot. |
| POST | `/technician/assignments/{id}/accept` | Acknowledge assignment (no state rewind). |
| POST | `/technician/assignments/{id}/reject` | Reject assignment → back to scheduling queue. |
| POST | `/technician/assignments/{id}/start-travel` | → `EN_ROUTE`. |
| POST | `/technician/assignments/{id}/check-in` | GPS-geofenced check-in → `ARRIVED` (creates a field visit + check-in record). |
| POST | `/technician/assignments/{id}/check-out` | Check-out (completes the visit, time entry). |
| POST | `/technician/assignments/{id}/start-work` | → `IN_PROGRESS`. |
| POST | `/technician/assignments/{id}/pause` | → `PAUSED`. |
| POST | `/technician/assignments/{id}/resume` | → `IN_PROGRESS`. |
| POST | `/technician/assignments/{id}/blocker` | Record a blocker → `BLOCKED`. |
| POST | `/technician/assignments/{id}/parts` | Request parts → `AWAITING_PARTS`. |
| POST | `/technician/assignments/{id}/remote-action` | Request remote activation via OSS adapter → `AWAITING_REMOTE_ACTION`. |
| POST | `/technician/assignments/{id}/checklist` | Submit versioned checklist responses (validated against item types/constraints). |
| POST | `/technician/assignments/{id}/proof` | Record proof of work (photo/serial/ack etc.). Duplicate `evidence_key` is idempotent. |
| POST | `/technician/assignments/{id}/materials` | Record material usage (consumed/returned). |
| POST | `/technician/assignments/{id}/devices` | Install a device (one device on one service). |
| POST | `/technician/assignments/{id}/acknowledgement` | Customer acknowledgement (OTP/signature). |
| POST | `/technician/assignments/{id}/finish` | Finish execution → `EXECUTION_COMPLETED` (validates checklist + proof + material reconciliation). |
| POST | `/technician/assignments/{id}/verify` | Submit for QA → `VERIFICATION_PENDING` (opens a QA review). |
| POST | `/technician/assignments/{id}/attachments` | Upload private evidence media (multipart; size + type restricted). |
| POST | `/technician/sync` | Offline batch sync (idempotent commands, version-conflict detection). |

## QA

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/qa/pending` | Pending/under-review work orders. |
| POST | `/qa/{work_order_id}/approve` | Approve QA (runs deterministic checks; requires checklist/proof/material completeness). Releases the technician. |
| POST | `/qa/{work_order_id}/reject` | Reject with reason → `QA_REJECTED` (rework) or `REJECTED`. |
| GET | `/work-orders/{id}/proof` | Proof-of-work list with verification state. |

## Field SLA

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/sla/policies` | Create a field SLA policy. |
| POST | `/sla/policies/{id}/versions` | Create a versioned policy definition + targets. |
| POST | `/sla/policies/{id}/activate` | Activate a version (only one active at a time). |
| GET | `/sla/at-risk` | All at-risk/breached field SLA instances. |

## Customer portal

Requires customer JWT (role `CUSTOMER`). Data is privacy-safe: no exact
technician location, no internal notes, no proof files.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/portal/appointments` | My appointments. |
| POST | `/portal/appointments/{id}/confirm` | Confirm an appointment window. |
| POST | `/portal/appointments/{id}/reschedule` | Request a reschedule. |
| GET | `/portal/work-orders/{id}` | Privacy-safe work-order status. |

## Reports / audit

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/reports/overview` | Open/unassigned/at-risk/breached/QA-pending/completed counts. |
| GET | `/reports/tickets` | Work-order count by status. |
| GET | `/audit` | Immutable administrative audit trail. |

## Health

`GET /health` and `GET /status` (public).

---

## Key domain rules

- **Numbering**: `WO-YYYY-NNNNNNNN`, per-tenant atomic sequence.
- **Assignment scoring** (`WEIGHTS`): skills 40, certifications 20, availability
  15, workload 10, proximity 8, service area 5, continuity 2. Every automatic
  assignment persists the breakdown (explainable); manual override always
  requires a reason.
- **Expired certifications** block assignments requiring them unless a
  supervisor records a certification exception.
- **GPS geofence**: check-in must be within the service-area radius (default
  500 m) or record a governed exception (INDOOR_GPS, LOW_ACCURACY, OFFLINE,
  WRONG_LOCATION, INFRASTRUCTURE_WORK). Supervisor overrides are audited.
- **Checklist**: the work order retains the exact template version; published
  versions are immutable. Responses are validated against item type
  (CHECKBOX, TEXT, SERIAL_NUMBER, MAC_ADDRESS, NUMBER, SELECT, PHOTO, SIGNATURE,
  OPTICAL_READING, SPEED_TEST, GPS_CAPTURE, …).
- **Proof of work**: server-side metadata (checksums, timestamps, device ref);
  files are private; download is authorization-controlled; duplicate
  `evidence_key` is idempotent (tenant-scoped).
- **Material reconciliation**: every required consumable must have sufficient
  usage (or an approved exception) before completion.
- **Device uniqueness**: one device (serial/MAC) cannot be installed on two
  active services — enforced locally and authoritatively by the inventory
  adapter.
- **Field SLA invariant**: `deadline = deadline_after(started_at,
  target + paused_accumulated_seconds)`. Policy-listed pause states
  (CUSTOMER_UNAVAILABLE, AWAITING_PARTS, AWAITING_REMOTE_ACTION,
  RESCHEDULE_REQUIRED) pause the clock; at-risk/breach evaluation is idempotent
  and restart-safe.
- **Offline sync**: client commands carry UUIDs + expected entity version.
  Duplicate retries are idempotent; stale commands (`entity_version <
  server`) are rejected; terminal work orders reject new commands.
- **Events**: transactional outbox (`workforce.*`) + idempotent consumer inbox
  for OSS order → work order, support ticket → work order, NMS repair → work
  order, inventory reservation confirmations, activation completed/failed, CRM
  customer updates and appointment confirmations.
