# Workforce Service

Field-operations foundation — **Master Implementation Spec Batch 2**.
`workforce_` tables, `workforce.events.v1` contracts, fail-closed tenant
isolation, append-only audit. Rebuilt after the source `.py` files were lost
(only `.pyc` caches remained).

## Capabilities (feature → implementation)

| Feature | Capability | Where |
|---|---|---|
| 329, 330, 333, 334 | Work-order lifecycle, assignment dispatch, on-site updates, job completion | `app/services.py:WorkOrderService`, `/api/workforce/v1/work-orders/*` |
| 337, 338, 339 | Device issuance, spare parts consumption, inventory sync | `app/services.py:InventoryService` |
| 342 | GPS location tracking (internal ingest) | `POST /api/workforce/v1/internal/ingest/location` |
| 344 | Shift scheduling | `app/services.py:ShiftService` |
| 346, 1490 | Technician KPI + productivity score | `app/services.py:KpiService` |
| 347 | Field SLA compliance | `app/services.py:SlaService` + worker sweep |
| 348 | Customer feedback | `app/services.py:FeedbackService` |
| 349 | Issue escalation | `app/services.py:EscalationService` |
| 1111 | Installation checklist validation | `app/services.py:ChecklistService` |
| 1112, 1113, 1115 | Site feasibility / power / signal / route checks | `app/services.py:FieldOpsService.site_check` |
| 1116 | Customer handover | `FieldOpsService.handover` |
| 1117 | Preventive maintenance scheduling | `app/tasks.py:schedule_preventive_maintenance` |
| 1118 | Emergency repair (work-order type EMERGENCY) | `WorkOrderService` |
| 1119 | Site visit logs | `app/services.py:VisitService` |
| 1423, 1486 | Network diagram/map read models | `GET /api/workforce/v1/dashboard/summary` |

## Run

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r ..\..\shared\runtime\requirements.txt -r requirements.txt
set DATABASE_URL=sqlite:///./workforce.db
set WORKFORCE_JWT_SECRET=<32+ chars>
set WORKFORCE_INTERNAL_API_KEY=<internal key>
uvicorn app.main:app --port 8013
python -m app.worker_runner
```

## Tests

```bash
python -m pytest
```
