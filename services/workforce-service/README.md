# Workforce Service (Milestone 6)

Field Workforce Management: a single canonical work-order model (separate from
OSS orders and support tickets), appointments, visits, check-in/out, technician
profiles with skills / certifications / availability / shifts, explainable
assignment scoring, dispatch planning and conflict detection, GPS geofenced
check-in with governed exceptions, versioned execution checklists, proof of
work with private media, a QA workflow (approve / reject / rework), field SLA
with calendars / pauses / rescheduling and at-risk / breach escalation,
inventory integration (reserve / issue / install / consume, one device on one
service) and offline-first mobile sync with idempotency and conflict rules.

Follows the same conventions as the OSS / CRM / AAA / support services
(FastAPI + SQLAlchemy + Alembic, transactional outbox / consumer inbox,
hermetic tests, tenant-isolated).

## Quick start

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r ..\..\shared\runtime\requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
# worker (field SLA evaluation, escalations, reminders, outbox flush):
.\.venv\Scripts\python.exe -m app.worker_runner
```

Run the tests:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## Environment

Copy `.env.example`. Key variables:

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | Service database (its own PostgreSQL DB; SQLite for tests) |
| `WORKFORCE_JWT_SECRET` | Management JWT (RBAC) — ≥ 32 chars |
| `WORKFORCE_TECHNICIAN_JWT_SECRET` | Technician mobile JWT — ≥ 32 chars |
| `WORKFORCE_CUSTOMER_JWT_SECRET` | Customer portal JWT — ≥ 32 chars |
| `WORKFORCE_INTERNAL_API_KEY` | Internal service-to-service auth |
| `WORKFORCE_CRM/SUPPORT/OSS/AAA/NETWORK/NMS/IPAM/INVENTORY/BILLING/NOTIFICATIONS_BASE_URL` | Cross-service adapter base URLs (unset = dependency reported unavailable) |
| `MAPS_PROVIDER` | `fake` (default, tests) / `google` / `alternative` — provider abstraction keeps the geofence logic testable without a live map API |
| `WORKFORCE_ATTACHMENT_DIR` | Private proof-of-work media storage (swap for object storage in production) |
| `WORKFORCE_GEOFENCE_RADIUS_M` | Geofence radius used when the service area has no explicit polygon |
| `RABBITMQ_URL` / `WORKFORCE_WORKER_INTERVAL` | Worker broker + cadence |

## Domain boundaries

- Workforce owns work orders, appointments, visits, technician profiles,
  dispatch, execution checklists, proof of work, QA and field SLA.
- It is the **single canonical field-work-order model** — OSS orders and support
  tickets reference field work; they do not define a second field-service
  system.
- It **never** touches another service's database, never runs device
  configuration directly (remote actions go through the OSS adapter), and never
  mutates inventory stock directly (reserve / issue / install / consume go
  through the inventory adapter with idempotency and one-device-one-service
  enforcement).

## API

See [`docs/apis/milestone-6-workforce.md`](../../docs/apis/milestone-6-workforce.md).
All work-order state changes are explicit command endpoints — there is no
`PATCH /work-orders/{id} {"status": ...}`.
