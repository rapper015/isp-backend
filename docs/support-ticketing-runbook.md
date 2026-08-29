# Support & Ticketing — Operations Runbook (Milestone 5)

This runbook covers the day-to-day operation, repair and troubleshooting of the
`support-service`.

## 1. Service layout

- API: `support-service` (FastAPI) behind the gateway at `/api/v1/support/`.
- Worker: `support-worker` runs `python -m app.worker_runner` for:
  - SLA evaluation (at-risk / breach)
  - Escalation checks (no-assignment, no-progress, repeated reopen, ...)
  - Auto-close of resolved tickets after the configured waiting period
  - Stuck-action timeout
  - Outbox flush to RabbitMQ
- Database: dedicated PostgreSQL database `support`.
- Attachments: private directory (`SUPPORT_ATTACHMENT_DIR`); swap in object
  storage for production. Never served as public URLs.

## 2. Health and dependency health

`GET /health` returns `{"status": "ok"}` — it reflects only the support process
itself. An unavailable customer router, NMS device or downstream service must
**not** mark the support service unhealthy. Dependency health is visible in
`GET /api/support/tickets/{id}/diagnostics` (per-source `status`:
`COMPLETE` / `PARTIAL` / `FAILED` / `SKIPPED`). A `FAILED` source is reported as
unavailable, never as healthy.

## 3. Ticket lifecycle quick reference

```
NEW ──▶ TRIAGE ──▶ ASSIGNED ──▶ IN_PROGRESS ──▶ RESOLVED ──▶ CLOSED
  │         │         │             │              ▲  │        │
  └─────────┴─────────┴─────────────┴── PENDING_CUSTOMER (SLA paused) ──┘
                                                          │
                                                          ▼
                                     ESCALATED ◀── automatic/manual triggers
```
Terminal states: `CLOSED`, `CANCELLED`, `DUPLICATE`. A ticket cannot be closed
without a resolution code and summary (duplicate/cancel flows are authorized
exceptions). Reopen from `RESOLVED`/`CLOSED`/`DUPLICATE` increments
`reopened_count`.

## 4. Common operations

### Find a ticket
`GET /api/support/tickets?tenant_id=...&search=TKT-2026-00001234` (also by
customer, subscriber username, SLA state, incident, etc.).

### Route / assign
- Routing rules decide queue/team/agent (`/api/support/routing`).
- Round-robin / least-loaded / skill / location strategies; fallback queue with
  loop prevention.
- Reassignments require a reason and append an event.

### Request a controlled action
1. `POST /tickets/{id}/actions/preview` — see preview + whether authorization
   is required (disruptive actions always require approval).
2. `POST /tickets/{id}/actions` — request (keep `idempotency_key` to avoid
   duplicates).
3. `POST /actions/{id}/approve` (disruptive), then `POST /actions/{id}/execute`.
4. Failed actions can be `retry`ed; the worker marks stuck `RUNNING` actions as
   `TIMED_OUT`.

The support service only requests operations through the authoritative services
(AAA for CoA/disconnect, Network Control for policy, OSS for orders, Workforce
for field jobs, BSS for billing/payment, IPAM for IP reconciliation). It never
executes RouterOS commands or edits RADIUS config directly.

### Correlate an outage
`POST /tickets/{id}/incidents/suggest` then `.../incidents/link`. When an
outage clears, `nms.outage_cleared` marks affected tickets
`ticket.outage_cleared_verification_pending` — the ticket is **not** auto-closed
until an agent verifies restoration.

### SLA
- Policies are versioned; a ticket snapshots the active version at creation.
  Later config changes never rewrite historical deadlines.
- `PENDING_CUSTOMER` pauses the timer; resuming extends deadlines by exactly the
  business time excluded.
- Authorized overrides: `POST /tickets/{id}/sla/override` (audited, reason
  required).
- `GET /api/support/sla/at-risk` lists at-risk / breached tickets.

## 5. Repair and maintenance commands (idempotent)

Run these through the worker's task functions (`app.tasks`) or a one-off
`python -c` against the service venv:

| Task | Purpose |
| --- | --- |
| `tasks.flush_outbox(session)` | Publish pending outbox events (retry-safe) |
| `tasks.evaluate_sla_deadlines(session)` | Re-evaluate SLA at-risk/breach (idempotent) |
| `tasks.run_escalation_checks(session)` | Detect no-assignment / no-progress / repeated-reopen |
| `tasks.auto_close_resolved(session)` | Auto-close resolved tickets past the waiting period |
| `tasks.requeue_stuck_actions(session)` | Mark RUNNING actions past timeout as TIMED_OUT |
| `tasks.detect_stuck_tickets(session)` | Find tickets with no update for N hours |
| `tasks.reconcile_sla_timers(session)` | Repair SLA deadlines to the persisted invariant |
| `sla_engine.reconcile_sla` | Reconcile one SLA instance |
| `assignment_service.detect_orphan_assignments` | Find tickets assigned to inactive agents |

All are restart-safe and idempotent (guarded by persisted state), so duplicate
scheduler executions are harmless.

## 6. Troubleshooting

| Symptom | Likely cause / action |
| --- | --- |
| Ticket created but no agent assigned | No routing rule matched and queue has no agents; route manually or add agents/rules. |
| SLA deadline didn't move while waiting on customer | Expected: `PENDING_CUSTOMER` pauses the clock. |
| Duplicate SLA events | Evaluation is guarded by `at_risk_at`/`breach_at`; check no two workers run with different DBs. |
| Duplicate inbound replies | Same `provider_message_id` is deduplicated (409). |
| Attachment download fails | Malware status `INFECTED`/`QUARANTINED`, wrong tenant/ticket, or file missing from storage. |
| Diagnostics show a source `FAILED` | The downstream service/adapter is unavailable — check its base URL env + health; do not pretend it is healthy. |
| Breach events after a pause | Pause only excludes business time during `pause_on_states`; re-check the policy definition. |

## 7. Security reminders

- Internal notes are never exposed to the portal API.
- Attachments are private and authorization-controlled; no permanent public URLs.
- Payment credentials, RouterOS passwords and RADIUS secrets must never be
  logged or returned. Billing context only exposes a summary and requires the
  `support.billing.summary.view` permission.
- Tenant/customer IDs are validated against the authenticated principal; never
  trust a client-supplied `tenant_id` for cross-tenant access.

## 8. Alerts / observability

Structured logs include `ticket_number`, `ticket_id`, `tenant`, queue, agent,
SLA state, escalation level, action id and correlation/causation ids. Metrics
to monitor: open tickets, unassigned tickets, at-risk tickets, SLA breaches,
first-response time, resolution time, reopen rate, escalation count,
support-action failures, message delivery failures, outbox backlog and
dead-letter count.
