# Support Service (Milestone 5)

Governed customer support and service management: tickets, lifecycle state
machine, versioned SLA timers, escalation, assignment/routing, deterministic
diagnostics, controlled operational actions, outage correlation, private
attachments, knowledge base and CSAT.

Follows the same conventions as the OSS/CRM/AAA services (FastAPI + SQLAlchemy
+ Alembic, transactional outbox / consumer inbox, hermetic tests).

## Quick start

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r ..\..\shared\runtime\requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
# worker (SLA evaluation, escalations, auto-close, outbox flush):
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
| `SUPPORT_JWT_SECRET` | Management JWT (RBAC) — ≥ 32 chars |
| `SUPPORT_CUSTOMER_JWT_SECRET` | Customer portal JWT — ≥ 32 chars |
| `SUPPORT_INTERNAL_API_KEY` | Internal service-to-service auth |
| `SUPPORT_CRM/BSS/OSS/AAA/NETWORK/NMS/IPAM/WORKFORCE/NOTIFICATIONS_BASE_URL` | Cross-service adapter base URLs (unset = dependency reported unavailable) |
| `SUPPORT_ATTACHMENT_DIR` | Private attachment storage (swap for object storage in production) |
| `RABBITMQ_URL` / `SUPPORT_WORKER_INTERVAL` | Worker broker + cadence |

## Domain boundaries

- Support owns tickets, SLA, escalation, queues, diagnostics, controlled actions, CSAT.
- It **never** touches another service's database, never runs arbitrary RouterOS
  commands, never edits RADIUS/FreeRADIUS config, never changes financial ledgers
  or allocates IPs. All of those go through adapters to the authoritative service.

## API

See [`docs/apis/milestone-5-support.md`](../../docs/apis/milestone-5-support.md).
All ticket state changes are explicit command endpoints — there is no
`PATCH /tickets/{id} {"status": ...}`.
