# Milestone 5 — Complete Frontend API Handoff: Support

Use the public gateway base URL and a bearer token; never use a Docker service
name or internal API key in browser code. On 401 refresh once and retry once;
show 403; refresh state on 409; map 422 detail to forms; back off on 429; retain
form data on 5xx. For multipart uploads let the browser set `Content-Type`.

## Deployment prerequisite — required before frontend work

The current gateway configuration has no `support-service` upstream or
`/api/support/` route. Therefore, these APIs are **not browser-reachable** in
the supplied Docker deployment. Do not hard-code a service-container URL.

Before enabling this milestone in a frontend, platform deployment must add a
protected gateway route that forwards `/api/support/` to the service, deploy a
browser-compatible Support JWT issuer/validator, set CORS for the frontend
origin, and verify `OPTIONS`, authenticated `GET`, and multipart attachment
upload through the gateway. Once deployed, use public base `/api/support/`.

## Operator support console

All routes below require the Support management bearer token and tenant scope.

| Screen | Reads | Mutations / behaviour |
| --- | --- | --- |
| Ticket inbox | `GET /tickets` with status, priority, assignee, customer, and date filters | Create with `POST /tickets`; navigate using returned ticket ID/number. |
| Ticket detail | `GET /tickets/{id}`, `/valid-actions`, `/events`, `/comments`, `/sla`, `/diagnostics` | Enable lifecycle controls exclusively from `valid-actions`. |
| Assignment queue | ticket detail + valid actions | `assign`, `reassign`, `transfer`, `accept`, `start-work`; refresh ticket after each state mutation. |
| Resolution | events, related resources, diagnostics | `request-info`, `escalate`, `resolve`, `close`, `reopen`, `cancel`, `duplicate`; confirmation-gate close/cancel/duplicate. |
| Conversation | comments | Use `reply` for customer-visible text and `note` for internal notes. Clearly label them in UI. |
| Attachments | attachment list | Upload multipart file, then use authenticated download endpoint as a Blob. |
| SLA management | policies, at-risk list, ticket SLA | Policy version → activate; use override only with permission and a recorded reason. |
| Safe actions | diagnostics, action preview/action list | Preview first, then create → approve (if required) → execute; poll/refresh action state. |
| Knowledge/routing | knowledge, catalogue, agents, routing | Administrative screens; publication and routing changes affect future tickets. |
| Reports/audit | reports overview/tickets, CSAT, audit | Treat as read-only server-side aggregates. |

## Ticket lifecycle integration

Create a ticket with `POST /api/support/tickets`. Keep the initial create form
limited to the fields exposed by the current schema: subject/description,
customer or service references, category, priority, channel, and optional
attachments or metadata. Let the server assign the ticket number, SLA, state,
and routing. Do not allow the UI to set a terminal state in the create call.

For every ticket detail page, fetch both `GET /tickets/{id}` and
`GET /tickets/{id}/valid-actions`. The backend controls lifecycle transitions;
the UI must not assume a linear sequence. After `resolve`, `close`, `reopen`,
or escalation, refetch the ticket, timeline, SLA, and actions rather than
optimistically altering counters or status chips.

## Attachments and portal isolation

Operator upload endpoint:

```text
POST /api/support/tickets/{ticket_id}/attachments
Content-Type: multipart/form-data
Authorization: Bearer <management-token>
```

Use `FormData`; do not manually set `Content-Type`. Render the backend-returned
original name and content type, but never interpolate the original name into
HTML. Download from `/attachments/{attachment_id}/download` as a Blob.

Customer portal endpoints live under `/api/support/portal/*` and use customer
authentication, not an operator token. Keep the operator console and customer
portal API clients/sessions separate. Never use an operator route to emulate a
customer action or expose internal notes, audit data, routing, or diagnostic
results in the customer portal.

## High-impact action rules

Actions generated from diagnostics can affect subscriber connectivity. The UI
must show the preview, requested action, required approval, and resulting state
before saying an action succeeded. A queued action is not an executed action.
For the complete, authoritative route inventory see
[`support-service/app/main.py`](../../services/support-service/app/main.py).

## Complete endpoint inventory

### Ticket core and collaboration

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/support/status` | Support API readiness/status. |
| POST / GET | `/api/support/tickets` | Create/list tickets. |
| GET | `/api/support/tickets/{ticket_id}` | Full ticket detail. |
| GET | `/api/support/tickets/{ticket_id}/valid-actions` | Authoritative enabled transitions. |
| GET | `/api/support/tickets/{ticket_id}/events` | Immutable ticket event timeline. |
| POST | `/api/support/tickets/{ticket_id}/{action}` | Lifecycle action where `{action}` is `assign`, `reassign`, `transfer`, `accept`, `start-work`, `request-info`, `escalate`, `resolve`, `close`, `reopen`, `cancel`, `duplicate`, `priority`, or `category`. |
| POST | `/api/support/tickets/{ticket_id}/reply` | Customer-visible reply. |
| POST | `/api/support/tickets/{ticket_id}/note` | Internal note. |
| GET | `/api/support/tickets/{ticket_id}/comments` | Replies and permitted notes. |
| POST / DELETE | `/api/support/tickets/{ticket_id}/watchers` | Add/remove watchers. |
| POST | `/api/support/tickets/{ticket_id}/related` | Add ticket relationship. |
| POST | `/api/support/tickets/{ticket_id}/attachments` | Multipart attachment upload. |
| GET | `/api/support/tickets/{ticket_id}/attachments/{attachment_id}/download` | Authenticated file download. |

### SLA, diagnosis, and controlled actions

| Method | Path | Purpose |
| --- | --- | --- |
| POST / GET | `/api/support/sla/policies` | Create/list SLA policies. |
| POST | `/api/support/sla/policies/{policy_id}/versions` | Add immutable version. |
| POST | `/api/support/sla/policies/{policy_id}/activate` | Activate policy. |
| GET | `/api/support/tickets/{ticket_id}/sla` | Ticket SLA clocks/state. |
| POST | `/api/support/tickets/{ticket_id}/sla/override` | Audited SLA override. |
| GET | `/api/support/sla/at-risk` | At-risk queue. |
| POST / GET | `/api/support/tickets/{ticket_id}/diagnostics/refresh`, `/api/support/tickets/{ticket_id}/diagnostics` | Refresh/read diagnostics. |
| POST | `/api/support/tickets/{ticket_id}/actions/preview` | Preview safe action. |
| POST / GET | `/api/support/tickets/{ticket_id}/actions` | Create/list controlled actions. |
| POST | `/api/support/actions/{action_id}/{action}` | Action workflow: `approve`, `execute`, `retry`, or `cancel`. |

### Cross-domain links, knowledge, routing, and reporting

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/support/tickets/{ticket_id}/incidents/suggest` | Suggest incident links. |
| POST | `/api/support/tickets/{ticket_id}/incidents/link` or `/unlink` | Manage incident link. |
| POST | `/api/support/tickets/{ticket_id}/orders/link` | Link OSS order. |
| POST | `/api/support/tickets/{ticket_id}/jobs/link` | Link workforce job. |
| POST | `/api/support/tickets/{ticket_id}/disputes/link` | Link billing dispute. |
| GET | `/api/support/csat` | CSAT reporting. |
| POST / GET | `/api/support/knowledge` | Create/search/list knowledge. |
| PUT | `/api/support/knowledge/{article_id}` | Update article. |
| POST | `/api/support/knowledge/{article_id}/publish` | Publish article. |
| POST | `/api/support/knowledge/{article_id}/usage` | Record article outcome/use. |
| POST | `/api/support/tickets/{ticket_id}/knowledge/suggest` | Suggest knowledge for ticket. |
| GET | `/api/support/catalog` | Support categories/catalogue. |
| POST | `/api/support/agents` | Create support agent. |
| POST / GET | `/api/support/routing` | Create/list routing rules. |
| GET | `/api/support/reports/overview` | Overview report. |
| GET | `/api/support/reports/tickets` | Ticket report. |
| GET | `/api/support/audit` | Append-only audit. |
| GET | `/api/support/tickets/{ticket_id}/billing-context` | Authorized billing context. |

### Customer portal and internal-only ingress

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/support/portal/me` | Customer portal identity. |
| POST / GET | `/api/support/portal/tickets` | Create/list customer's own tickets. |
| GET | `/api/support/portal/tickets/{ticket_id}` | Customer-safe ticket detail. |
| POST | `/api/support/portal/tickets/{ticket_id}/reply` | Customer reply. |
| POST | `/api/support/portal/tickets/{ticket_id}/confirm` | Confirm resolution. |
| POST | `/api/support/portal/tickets/{ticket_id}/reopen` | Request reopen. |
| POST | `/api/support/portal/tickets/{ticket_id}/csat` | Submit satisfaction rating. |
| POST | `/api/support/inbound` | Internal-only channel ingress; never call from browser. |
