# API Documentation — Milestone 5: Support & Ticketing

Service: `support-service` — served at `/api/v1/support/` through the gateway
(and `/api/support/` on the service itself).

This doc is generated from the route registrations in `app/main.py`; keep it in
sync when routes change.

## Authentication

| Surface | Scheme | Header |
| --- | --- | --- |
| Management APIs | Management JWT (RBAC) | `Authorization: Bearer <jwt>` (secret `SUPPORT_JWT_SECRET`) |
| Customer portal | Customer JWT | `Authorization: Bearer <jwt>` (secret `SUPPORT_CUSTOMER_JWT_SECRET`) |
| Inbound message ingestion | Internal service key | `X-Internal-API-Key` |

Tenant scope is taken from the authenticated principal. A client-supplied
`tenant_id` (query or body) is only honoured when it matches the principal; a
mismatch returns `403`.

Roles: `SUPPORT_MANAGER`, `SUPERVISOR`, `L2_SUPPORT`, `L1_SUPPORT`,
`NOC_ENGINEER`, `BILLING_SUPPORT`, `FIELD_COORDINATOR`, `CUSTOMER_CARE`,
`AUDITOR`, `READ_ONLY`, plus platform roles. Permissions map to `support.*`
grants (view / create / assign / transfer / escalate / internal_note /
public_reply / resolve / close / reopen / cancel / mark_duplicate /
billing.summary.view / diagnostic.view / diagnostic.run / action.request /
action.approve / action.execute / outage.link / sla.manage / catalog.manage /
routing.manage / kb.manage / report.view / audit.view / export).

## Health

- `GET /health` — `{"status": "ok"}`
- `GET /status` — `{"service": "support", "phase": "milestone-5-support-ticketing"}`
- `GET /api/support/status` — capability list

## Tickets — management

All ticket commands are explicit; status is never PATCHed.

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/support/tickets` | Create ticket (body includes `tenant_id`, `ticket_type`, `subject`, `description`, references, category, impact/urgency/priority) |
| GET | `/api/support/tickets` | List/search/filter (`status`, `ticket_type`, `priority`, `queue_id`, `assigned_agent_id`, `customer_id`, `subscriber_username`, `sla_status`, `nms_incident_id`, `search`) |
| GET | `/api/support/tickets/{id}` | Detail + events + watchers + tags |
| GET | `/api/support/tickets/{id}/valid-actions` | Allowed transitions from current state |
| GET | `/api/support/tickets/{id}/events` | Immutable event stream |
| POST | `/api/support/tickets/{id}/assign` | Assign agent (`agent_id`, `reason`) |
| POST | `/api/support/tickets/{id}/reassign` | Reassign (reason required) |
| POST | `/api/support/tickets/{id}/transfer` | Transfer queue |
| POST | `/api/support/tickets/{id}/accept` | Accept / start work |
| POST | `/api/support/tickets/{id}/start-work` | Start work |
| POST | `/api/support/tickets/{id}/request-info` | Request customer information (→ PENDING_CUSTOMER, SLA paused) |
| POST | `/api/support/tickets/{id}/escalate` | Escalate with reason + trigger |
| POST | `/api/support/tickets/{id}/resolve` | Resolve (resolution_code + summary required) |
| POST | `/api/support/tickets/{id}/close` | Close (only from RESOLVED; resolution data required) |
| POST | `/api/support/tickets/{id}/reopen` | Reopen with reason |
| POST | `/api/support/tickets/{id}/cancel` | Cancel with reason |
| POST | `/api/support/tickets/{id}/duplicate` | Mark duplicate of another ticket |
| POST | `/api/support/tickets/{id}/priority` | Change priority (reason required; SLA recalculated) |
| POST | `/api/support/tickets/{id}/category` | Change category/subcategory |
| POST | `/api/support/tickets/{id}/reply` | Add public reply (HTML sanitized) |
| POST | `/api/support/tickets/{id}/note` | Add internal note (never customer-visible) |
| GET | `/api/support/tickets/{id}/comments` | Comments (agents see all; portal sees public only) |
| POST | `/api/support/tickets/{id}/attachments` | Upload attachment (multipart) |
| GET | `/api/support/tickets/{id}/attachments/{aid}/download` | Authorization-controlled download |
| POST/DELETE | `/api/support/tickets/{id}/watchers` | Add/remove watcher |
| POST | `/api/support/tickets/{id}/related` | Link parent/child/linked relationship |
| GET | `/api/support/tickets/{id}/billing-context` | Billing summary (authorized roles) |

## Inbound messaging

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/support/inbound` | Ingest inbound email/WhatsApp/webhook message (threading by ticket id / number / reply token; dedupe by `provider_message_id`) |

## SLA

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/support/sla/policies` | Create policy |
| POST | `/api/support/sla/policies/{id}/versions` | Create (and optionally activate) immutable version |
| POST | `/api/support/sla/policies/{id}/activate` | Activate a version |
| GET | `/api/support/sla/policies` | List policies + versions + targets |
| GET | `/api/support/tickets/{id}/sla` | Ticket SLA timeline (deadlines, pauses, events) |
| POST | `/api/support/tickets/{id}/sla/override` | Authorized audited SLA override |
| GET | `/api/support/sla/at-risk` | At-risk / breached tickets |

## Diagnostics

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/support/tickets/{id}/diagnostics/refresh` | Capture a fresh diagnostic snapshot |
| GET | `/api/support/tickets/{id}/diagnostics` | Latest snapshot (sources + checks) |

## Controlled support actions

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/support/tickets/{id}/actions/preview` | Preview action (disruptive/authorization preview) |
| POST | `/api/support/tickets/{id}/actions` | Request action (idempotency key supported) |
| POST | `/api/support/actions/{id}/approve` | Approve (disruptive actions) |
| POST | `/api/support/actions/{id}/execute` | Execute through authoritative adapter |
| POST | `/api/support/actions/{id}/retry` | Retry a failed action |
| POST | `/api/support/actions/{id}/cancel` | Cancel a pending action |
| GET | `/api/support/tickets/{id}/actions` | List ticket actions |

## Outage / incident / order / job / dispute correlation

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/support/tickets/{id}/incidents/suggest` | Suggest matching NMS incidents |
| POST | `/api/support/tickets/{id}/incidents/link` | Link ticket to incident |
| POST | `/api/support/tickets/{id}/incidents/unlink` | Unlink incident |
| POST | `/api/support/tickets/{id}/orders/link` | Link OSS order |
| POST | `/api/support/tickets/{id}/jobs/link` | Link Workforce job |
| POST | `/api/support/tickets/{id}/disputes/link` | Link billing dispute |

## Knowledge / CSAT / Catalog / Routing / Reports

| Method | Path | Purpose |
| --- | --- | --- |
| POST/PUT | `/api/support/knowledge`, `/api/support/knowledge/{id}` | Create / update article (edits re-draft) |
| POST | `/api/support/knowledge/{id}/publish` | Publish (approve) |
| GET | `/api/support/knowledge` | Search |
| POST | `/api/support/tickets/{id}/knowledge/suggest` | Category/type suggestions |
| POST | `/api/support/knowledge/{id}/usage` | Record article usage |
| GET | `/api/support/csat` | CSAT records |
| GET | `/api/support/catalog` | Types/categories/subcategories/queues/teams |
| POST | `/api/support/agents` | Add agent to team (skills/locations) |
| POST/GET | `/api/support/routing` | Add / list routing rules |
| GET | `/api/support/reports/overview` | Open/unassigned/at-risk/breached/reopened/CSAT counts |
| GET | `/api/support/reports/tickets` | Ticket counts by status |
| GET | `/api/support/audit` | Administrative audit log |

## Customer portal

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/support/portal/me` | Current principal |
| POST | `/api/support/portal/tickets` | Create own ticket |
| GET | `/api/support/portal/tickets` | List own tickets |
| GET | `/api/support/portal/tickets/{id}` | Safe detail + public timeline + expected deadlines/visit |
| POST | `/api/support/portal/tickets/{id}/reply` | Reply (resumes PENDING_CUSTOMER) |
| POST | `/api/support/portal/tickets/{id}/confirm` | Confirm resolution (→ closed) |
| POST | `/api/support/portal/tickets/{id}/reopen` | Reopen within policy |
| POST | `/api/support/portal/tickets/{id}/csat` | Submit satisfaction |

Portal responses exclude internal notes, staff diagnostics, escalation detail
and full audit metadata; attachments and comments are tenant + customer scoped.

## Events

Publishes (transactional outbox → `support.events.v1`): `support.ticket.created`,
`support.ticket.assigned`, `support.ticket.priority_changed`,
`support.ticket.escalated`, `support.ticket.customer_replied`,
`support.ticket.public_reply`, `support.ticket.sla_at_risk`,
`support.ticket.sla_breached`, `support.ticket.support_action_requested`,
`support.ticket.support_action_completed`, `support.ticket.resolved`,
`support.ticket.closed`, `support.ticket.reopened`, `support.ticket.csat_received`,
`support.ticket.outage_linked`, `support.ticket.oss_order_linked`,
`support.ticket.workforce_job_linked`, `support.problem.created`,
`support.major_incident.declared` (all `.v1`).

Consumes (idempotent inbox): `crm.customer.updated`, `oss.service.*`,
`oss.order.completed/failed`, `bss.payment.captured`,
`bss.billing.account_delinquent`, `aaa.session.started/stopped`,
`nms.incident_created`, `nms.outage_detected/cleared`,
`workforce.job_completed` (all `.v1`).
