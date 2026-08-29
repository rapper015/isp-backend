# Workforce Dispatch Runbook (Milestone 6)

Operational runbook for day-to-day field dispatch, escalations and the field
SLA worker.

## Scope

- `workforce-service` (API) — work orders, technician profiles, dispatch, QA,
  field SLA.
- `workforce-worker` — periodic tasks: outbox flush, field SLA evaluation,
  escalations, appointment reminders, stuck/orphan detection, certification
  expiry.
- Broker: RabbitMQ. Cache/locks: Valkey (never source of truth).

## Normal flow: new installation

1. **Order → work order**. OSS emits `oss.order.field_work_required.v1`; the
   consumer creates a canonical `NEW_INSTALLATION` work order with template and
   checklist snapshots and an active field SLA.
2. **Validate + schedule**. `POST /work-orders/{id}/validate` then
   `POST /work-orders/{id}/schedule` with a window. The appointment enters
   `CUSTOMER_CONFIRMATION_PENDING` and a confirmation-request event is
   published.
3. **Customer confirms**. `POST /portal/appointments/{id}/confirm`.
4. **Assign + dispatch**. `POST /work-orders/{id}/assign`
   (`strategy=SKILL_BASED` or a manual technician with a reason), then
   `POST /work-orders/{id}/dispatch`.
5. **Technician mobile**. accept → start-travel → check-in (GPS geofence) →
   start-work → checklist → proof → materials → device → finish → verify.
6. **QA**. `POST /qa/{id}/approve` (or reject → rework). Completion releases
   the technician and marks the SLA `COMPLETED`.

## Dispatch daily cadence

- Pull the board: `GET /dispatch/board?date=YYYY-MM-DD`.
- See unassigned: `GET /dispatch/unassigned`.
- Get recommendations: `GET /dispatch/recommendations/{work_order_id}` — review
  the persisted score breakdown before overriding.
- Validate a proposed assignment (conflicts/availability): 
  `POST /dispatch/validate-assignment`.
- Route plan: `POST /dispatch/plans/{technician_id}/sequence` (optimistic
  version; a stale edit is rejected rather than silently overwriting a
  confirmed customer appointment).

## Field SLA worker behaviour

- The worker evaluates active SLA instances (`evaluate_field_slas`) and marks
  `AT_RISK` / `BREACHED`. Evaluation is idempotent and restart-safe — duplicate
  scheduler runs never double-emit breach events.
- Escalation: `run_escalations` applies policy actions (notify dispatcher /
  supervisor, require manual intervention) once per threshold.
- **Pauses**: policy-listed states (`CUSTOMER_UNAVAILABLE`, `AWAITING_PARTS`,
  `AWAITING_REMOTE_ACTION`, `RESCHEDULE_REQUIRED`) pause the business clock;
  resuming restarts it with accumulated pause seconds. `GET /work-orders/{id}/sla`
  shows the full timeline.
- **Supervisor exception**: a missed deadline can be corrected deliberately via
  `POST /work-orders/{id}/sla/exception` (audited) — never by silently editing
  deadlines in the database.

## Escalation playbook

| Signal | Check | Action |
| --- | --- | --- |
| Work order `AT_RISK` | `GET /sla/at-risk` | Prioritize/reassign via `POST /work-orders/{id}/reassign` with a reason. |
| Work order `BREACHED` | SLA timeline | Customer comms + documented exception or root-cause follow-up. |
| Blocked execution | `work_order.blocked` event | Dispatch supervisor review; resolve or fail the work order. |
| Awaiting parts | `AWAITING_PARTS` | Reserve/issue materials; resume when stock confirmed. |
| Awaiting remote action | `AWAITING_REMOTE_ACTION` | OSS activation completion auto-resumes the work order. |
| Stuck > threshold | `detect_stuck_work_orders` | Review and take explicit action (resume/fail/cancel). |
| Orphan assignment | `detect_orphan_assignments` | Reassign or release the technician. |

## Recovery

- **Worker restart**: re-run `python -m app.worker_runner`; tasks are
  idempotent.
- **Duplicate scheduler**: harmless (evaluation idempotency + outbox flush).
- **Broker down**: outbox rows accumulate with `published_at IS NULL`; worker
  flushes when RabbitMQ returns. No data is lost (outbox is source of truth
  until published).
- **DB restore**: apply Alembic migrations, then let the worker re-evaluate SLA
  instances (deadlines are stored, not recomputed from scratch).

## Runbook invariants (do not violate)

1. Never set `work_order.status` directly — use command endpoints.
2. Never edit a field SLA deadline directly — use the exception endpoint.
3. Never install a device outside the inventory adapter (uniqueness).
4. Never fabricate GPS coordinates — record a governed exception.
5. Media files live in `WORKFORCE_ATTACHMENT_DIR` with auth-controlled
   download; no permanent public URLs.
