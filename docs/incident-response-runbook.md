# Incident Response Runbook

End-to-end incident management through the assurance-service.

## Lifecycle

```
DETECTED → TRIAGE → INVESTIGATING → IDENTIFIED → MITIGATING → MONITORING → RESOLVED → CLOSED
                                              ↘ (major / requires analysis) POSTMORTEM_REQUIRED → CLOSED
```

Transitions are validated; `RESOLVED` is only reachable via `MONITORING`.

## 1. Detect & triage

- Alert fires → ACK → create incident (optionally auto from alert).
- Assign a **commander** and **responders**:
  `POST /incidents/{id}/commanders`, `POST /incidents/{id}/responders`.
- Declare **major** for customer-facing/high-severity:
  `POST /incidents/{id}/major`.

## 2. Impact

- **Estimate first** (never wait for exact numbers):
  `POST /incidents/{id}/impact-estimate` (`impact_kind`, `estimated_subscribers`).
- **Confirm later** from verified data:
  `POST /incidents/{id}/impact-confirm` (`confirmed_subscribers`).
- Estimated and confirmed are stored separately; the summary reports both.
- Add affected services: `POST /incidents/{id}/service-impact`.

## 3. Investigate (root cause)

Evidence-based only:

1. Create a hypothesis: `POST /incidents/{id}/root-causes`.
2. Attach evidence: `POST /root-causes/{id}/evidence`
   (`supports: true|false`).
3. Move through `OBSERVATION → HYPOTHESIS → LIKELY_CAUSE`.
4. Confirm: `POST /root-causes/{id}/confirm` — requires **≥1 supporting
   evidence, no contradicting evidence, and explicit human confirmation**.
   **Temporal coincidence is never auto-confirmed.**

AI suggestions (`is_ai_suggestion`) never auto-confirm; a human must confirm.

## 4. Link the support ticket

`POST /incidents/{id}/tickets` — the ticket is **linked**, never conflated with
the incident record. Customer comms go through `POST /incidents/{id}/communications`
(`audience: INTERNAL | CUSTOMER_SAFE`).

## 5. Mitigate, monitor, resolve

- Mitigation actions: `POST /incidents/{id}/actions`.
- Transition `MITIGATING → MONITORING → RESOLVED` only after sustained recovery.
- `RESOLVED` records `resolved_at`.

## 6. Postmortem

- Require postmortem: `POST /incidents/{id}/require-postmortem` → state
  `POSTMORTEM_REQUIRED`.
- Create: `POST /postmortems` (summary, root cause, timeline).
- Add action items: `POST /postmortems/{id}/actions`. Required action items
  **cannot be deleted**; close them by completing.
- Transition incident `CLOSED` when the postmortem is approved and action
  items are tracked.

## Severity → impact mapping

- LOW + customer impact → effective MEDIUM.
- HIGH on customer-facing path with confirmed impact → treat as major.
