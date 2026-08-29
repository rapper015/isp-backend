# Support SLA — Configuration Guide (Milestone 5)

This guide explains how SLA policies, versions, targets, business calendars and
escalation thresholds work in the support service, and how to configure them.

## 1. Concepts

| Entity | Purpose |
| --- | --- |
| `SLAPolicy` | A named SLA contract (`code`, `name`, active flag). |
| `SLAPolicyVersion` | An **immutable** published revision of the policy definition. Once published, it is never edited; activating a new version starts a new revision. |
| `SLATarget` | Per-priority business-second targets for a version (e.g. P1 response 15 min). `ALL` is the fallback for priorities without a specific row. |
| `BusinessCalendar` | Working hours per weekday (`mon`..`sun`), timezone. |
| `Holiday` | Non-working dates for a calendar. |
| `TicketSLA` | The authoritative per-ticket SLA instance: immutable policy snapshot + exact timer state + deadlines. |

## 2. How a ticket gets its SLA

1. On ticket creation, the service selects the **tenant's active policy**,
   falling back to the platform **global** policy. The selection reason is
   recorded.
2. The active **version** of that policy is resolved, and `SLATarget` rows are
   read for the ticket priority (`ALL` if no priority row exists).
3. Deadlines are computed from the business calendar:
   `deadline = deadline_after(started_at, target_business_seconds + paused_seconds)`.
4. The full policy definition + calendar + targets are snapshotted into the
   `TicketSLA.policy_snapshot`. Later config changes never rewrite these
   historical deadlines.

## 3. Timer behaviour

- **Business time** = time within working hours, excluding holidays, in the
  calendar timezone.
- **Pause**: entering a state listed in `definition.pause_on_states` (default
  `["PENDING_CUSTOMER"]`) pauses the clock. Paused intervals are recorded.
- **Resume**: leaving a pause state extends both deadlines by exactly the
  business time that the pause excluded, then recomputes.
- **Reassignment** does **not** reset timers (unless the policy says otherwise).
- **Priority change** preserves the elapsed fraction and re-derives deadlines
  from the new priority's targets.
- **Reopen**:
  - `reopen_policy: "RESTART"` (default) → fresh full-target deadlines.
  - `reopen_policy: "CONTINUE"` → preserves remaining time.
- **First human response**: a system acknowledgement does **not** count; the
  response target is only met when an agent sends a public reply.

## 4. At-risk and breach

`definition.escalation` is a list of thresholds:

```json
{
  "escalation": [
    {"target": "RESPONSE",   "at_risk_pct": 75, "level": 1, "action": "NOTIFY_AGENT"},
    {"target": "RESOLUTION", "at_risk_pct": 75, "level": 1, "action": "NOTIFY_TEAM_LEAD"},
    {"target": "RESOLUTION", "at_risk_pct": 90, "level": 2, "action": "ADD_SUPERVISOR_WATCHER"}
  ]
}
```

- At risk when the remaining business seconds drop to ≤ `at_risk_pct` % of the
  target. The transition fires **once** (guarded by `at_risk_at`).
- Breach fires **once** when `now >= deadline` (guarded by `breach_at`) and
  records an immutable event + outbox message; a delayed notification never
  changes the actual breach timestamp.
- The worker evaluates periodically (`SUPPORT_WORKER_INTERVAL`); evaluation is
  idempotent and restart-safe.

## 5. Configuration via API

```bash
# 1. Create a policy
POST /api/support/sla/policies?tenant_id=...   {"code": "PREMIUM", "name": "Premium SLA"}

# 2. Publish + activate version 1
POST /api/support/sla/policies/<id>/versions?tenant_id=...
{
  "definition": {
    "pause_on_states": ["PENDING_CUSTOMER"],
    "reopen_policy": "RESTART",
    "reset_on_reassign": false,
    "acknowledgement_counts_as_first_response": false,
    "escalation": [
      {"target": "RESPONSE",   "at_risk_pct": 75, "level": 1, "action": "NOTIFY_AGENT"},
      {"target": "RESOLUTION", "at_risk_pct": 75, "level": 1, "action": "NOTIFY_TEAM_LEAD"}
    ]
  },
  "targets": [
    {"priority": "ALL",          "kind": "RESPONSE",   "business_seconds": 14400},
    {"priority": "ALL",          "kind": "RESOLUTION", "business_seconds": 28800},
    {"priority": "P1_CRITICAL",  "kind": "RESPONSE",   "business_seconds": 900},
    {"priority": "P1_CRITICAL",  "kind": "RESOLUTION", "business_seconds": 7200},
    {"priority": "P2_HIGH",      "kind": "RESPONSE",   "business_seconds": 1800},
    {"priority": "P2_HIGH",      "kind": "RESOLUTION", "business_seconds": 14400}
  ],
  "activate": true
}

# 3. (Optional) activate a later version
POST /api/support/sla/policies/<id>/activate?tenant_id=...  {"version": 2}
```

## 6. Business calendar

A default calendar (`DEFAULT`, Mon–Fri 09:00–18:00, Sat 10:00–14:00, UTC) is
seeded globally. Tenant-specific calendars are created on demand. Working hours
are stored as JSON on the calendar row:

```json
{
  "mon": [["09:00", "18:00"]],
  "tue": [["09:00", "18:00"]],
  "sat": [["10:00", "14:00"]],
  "sun": []
}
```

`24:00` is accepted as end-of-day. Holidays are stored as date rows per
calendar. Timezone is the IANA name (e.g. `Asia/Kolkata`).

## 7. Authorized override

`POST /api/support/tickets/{id}/sla/override?tenant_id=...` with
`response_deadline`, `resolution_deadline` and `reason` — requires the
`support.sla.manage` permission, records an audit row and an immutable
`ticket.sla_override` event, and does not rewrite the policy snapshot.

## 8. Best practices

- Prefer specific priority targets for P1/P2; keep `ALL` as the safe baseline.
- Keep `at_risk_pct` below 100 and consistent with your monitoring cadence.
- Publishing a new version does **not** touch existing tickets; use a supervised
  migration (reconcile/override) only when a deliberate global change is
  required.
- Use `tasks.reconcile_sla_timers` after any manual data repair to restore the
  `deadline = deadline_after(started_at, target + paused)` invariant.
