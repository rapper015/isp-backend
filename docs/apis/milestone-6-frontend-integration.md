# Milestone 6 — Complete Frontend API Handoff: Workforce

Use the public gateway base URL and bearer token. On 401 refresh once/retry
once; show 403; refresh on 409; map 422 detail; back off on 429; preserve form
data on 5xx. Public base is `/api/workforce/v1/` through either gateway prefix
`/api/workforce/` or `/api/v1/workforce/`. Use a management bearer token and
tenant context. `/internal/ingest/location` is backend-only.

## Core screens and route map

| Screen | Routes | Frontend behaviour |
| --- | --- | --- |
| Technician directory | `POST/GET /technicians`, `/{id}/status` | Use server status; do not infer availability solely from a shift. |
| Work order board | `POST/GET /work-orders`, `/{id}` | Filter server-side and group by backend `status`. |
| Dispatch | `GET /dispatch/suggest`, `/{id}/assign`, `/{id}/dispatch` | Display suggestions as recommendations; assign ID and schedule explicitly. |
| Technician workflow | `/{id}/transition`, `/{id}/complete` | Disable duplicate actions and refetch the work order after each transition. |
| Field evidence | `/site-checks`, `/visits`, `/proof`, `/handover` | Evidence uses a stored `evidence_key`; upload/storage acquisition is separate from this API. |
| Inventory | item create/list, issue, return, sync, consumables/consume | Use backend inventory status as authoritative. |
| Scheduling | `POST /shifts`, checklists/validate | Validate checklist before dispatch/completion UX. |
| Quality / escalation | feedback, escalations, SLA evaluate, technician KPI | Clearly separate customer feedback from operational escalation. |
| Operations dashboard | dashboard summary, audit log, expert sessions, visualizations, overlays, spare parts | Present dashboard/audit data read-only unless a listed mutation exists. |

## Request bodies used by the core workflow

Create a technician:

```json
{"name":"Ravi Kumar","phone":"9876543210","email":"ravi@example.com","skills":["FIBER"],"territories":["PUNE-NORTH"]}
```

Create a work order:

```json
{
  "title":"Install fibre connection",
  "type":"INSTALLATION",
  "customer_id":"<crm-customer-id>",
  "address":"12 Example Road, Pune",
  "priority":"HIGH",
  "source_ticket_id":"<support-ticket-id>",
  "sla_minutes":240
}
```

Assign it:

```json
{
  "technician_id":"<technician-uuid>",
  "scheduled_start":"2026-09-15T04:30:00Z",
  "scheduled_end":"2026-09-15T06:30:00Z",
  "notes":"Call customer before arrival"
}
```

Transition body is `{ "transition": "<server-supported-transition>",
"note": "optional" }`. Do not hard-code an enum beyond the deployed backend:
render a validation error and refresh if the transition is rejected.

## Dispatch-to-completion workflow

1. Create or open a work order and store the returned UUID/ref ID.
2. Load dispatch suggestions, then assign a technician and scheduled window.
3. Dispatch only after the assignment has succeeded.
4. Record site checks, visit records, and proof as they occur. A proof request
   needs an `evidence_key`, `kind`, and optional visit/work-order IDs.
5. Validate the checklist using `POST /work-orders/{id}/checklist/validate`.
6. Complete only when server state and checklist validation allow it; refresh
   detail, SLA, inventory, and assignment afterwards.

Use polling/refresh for changes made by another dispatcher or technician.
`POST /sla/evaluate` is an operational calculation, not a customer-facing
success confirmation. The actual API schemas live in
[`workforce-service/app/schemas.py`](../../services/workforce-service/app/schemas.py).

## Complete endpoint inventory

All paths below are service paths under the public Workforce gateway mapping.

| Method | Path | Purpose |
| --- | --- | --- |
| POST / GET | `/api/workforce/v1/technicians` | Create/list technicians. |
| POST | `/api/workforce/v1/technicians/{tech_id}/status` | Change technician availability/status. |
| POST / GET | `/api/workforce/v1/work-orders` | Create/list work orders. |
| GET | `/api/workforce/v1/work-orders/{wo_id}` | Work-order detail. |
| POST | `/api/workforce/v1/work-orders/{wo_id}/assign` | Assign technician and schedule. |
| POST | `/api/workforce/v1/work-orders/{wo_id}/dispatch` | Dispatch assigned work. |
| POST | `/api/workforce/v1/work-orders/{wo_id}/transition` | Apply lifecycle transition. |
| POST | `/api/workforce/v1/work-orders/{wo_id}/complete` | Complete validated work. |
| GET | `/api/workforce/v1/dispatch/suggest` | Ranked dispatch suggestions. |
| POST | `/api/workforce/v1/checklist-templates` | Create checklist template. |
| POST | `/api/workforce/v1/work-orders/{wo_id}/checklist/validate` | Validate completed checklist items. |
| POST / GET | `/api/workforce/v1/work-orders/{wo_id}/site-checks` | Record/list site checks. |
| POST | `/api/workforce/v1/work-orders/{wo_id}/visits` | Record a field visit. |
| POST / GET | `/api/workforce/v1/work-orders/{wo_id}/proof` | Record/list evidence references. |
| POST | `/api/workforce/v1/work-orders/{wo_id}/handover` | Record customer/operations handover. |
| POST / GET | `/api/workforce/v1/inventory/items` | Create/list serialized inventory. |
| POST | `/api/workforce/v1/inventory/items/{item_id}/issue` | Issue item to work/technician. |
| POST | `/api/workforce/v1/inventory/items/{item_id}/return` | Return issued item. |
| POST | `/api/workforce/v1/inventory/sync` | Reconcile inventory state. |
| POST | `/api/workforce/v1/inventory/consumables` | Create consumable stock. |
| POST | `/api/workforce/v1/inventory/consumables/consume` | Consume stock against work. |
| POST | `/api/workforce/v1/shifts` | Create technician shift. |
| POST | `/api/workforce/v1/work-orders/{wo_id}/feedback` | Record customer feedback. |
| GET | `/api/workforce/v1/feedback` | List feedback. |
| POST | `/api/workforce/v1/escalations` | Create escalation. |
| POST | `/api/workforce/v1/escalations/{esc_id}/resolve` | Resolve escalation. |
| POST | `/api/workforce/v1/sla/evaluate` | Evaluate work-order SLA. |
| GET | `/api/workforce/v1/kpis/technician/{tech_id}` | Technician KPI projection. |
| GET | `/api/workforce/v1/dashboard/summary` | Operations dashboard summary. |
| GET | `/api/workforce/v1/audit-log` | Workforce audit log. |
| POST / GET | `/api/workforce/v1/expert/sessions` | Start/list expert-assistance sessions. |
| POST | `/api/workforce/v1/expert/sessions/{session_id}/end` | End expert session. |
| POST / GET | `/api/workforce/v1/failure/visualizations` | Create/list failure visualizations. |
| POST | `/api/workforce/v1/failure/visualizations/{vis_id}/rendered` | Mark visualization rendered. |
| POST / GET | `/api/workforce/v1/equipment/overlays` | Create/list equipment overlays. |
| POST / GET | `/api/workforce/v1/spare-parts` | Create/list spare parts. |
| POST | `/api/workforce/v1/spare-parts/{part_id}/use` | Record spare-part use. |
| POST | `/api/workforce/v1/internal/ingest/location` | Internal-only technician location ingest; never call from browser. |
