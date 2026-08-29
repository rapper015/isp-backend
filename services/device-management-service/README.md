# Device Management Service (Milestone 7)

Business-facing control plane for TR-069 CPE management that securely
integrates with a **separately deployed GenieACS** CWMP service. GenieACS owns
protocol sessions, the parameter tree, RPC execution and pending tasks; this
service owns tenant ownership, device business identity, the device-model
catalogue, vendor-neutral versioned profiles, configuration jobs with
read-back verification, controlled actions, capability-aware diagnostics,
firmware approval and phased rollouts, drift, reconciliation, RBAC and full
auditability.

It follows the same conventions as the OSS/CRM/AAA/support/workforce services
(FastAPI + SQLAlchemy + Alembic, transactional outbox / consumer inbox,
hermetic tests, tenant isolation).

## Quick start

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r ..\..\shared\runtime\requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
# worker (outbox flush, job task polling, timeouts, drift, rollouts):
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
| `DEVICE_MANAGEMENT_JWT_SECRET` | Management JWT (RBAC) — ≥ 32 chars |
| `DEVICE_MANAGEMENT_INTERNAL_API_KEY` | Internal service-to-service auth |
| `DEVICE_MANAGEMENT_ENCRYPTION_KEY` | Encrypts CPE/ACS secret references |
| `ACS_PROVIDER` | `fake` (default, tests) or `genieacs` |
| `GENIEACS_BASE_URL` | GenieACS NBI base URL |
| `DEVICE_CRM/OSS/INVENTORY/WORKFORCE/SUPPORT/NMS_BASE_URL` | Cross-service adapter base URLs (unset = dependency reported unavailable) |
| `DEVICE_FIRMWARE_DIR` | Private firmware storage |
| `DEVICE_MGMT_WORKER_INTERVAL` / `DEVICE_MGMT_JOB_TIMEOUT_MINUTES` | Worker cadence + job timeout |
| `RABBITMQ_URL` | Worker broker |

## Domain boundaries

- Device Management is the business control plane; GenieACS is the CWMP/ACS
  engine. The backend never queries GenieACS MongoDB and never exposes the
  GenieACS NBI to tenant frontends — all interaction goes through the
  `ACSClient` adapter.
- Physical inventory custody is authoritative elsewhere; this service keeps a
  **separate** managed-CPE identity/operational state.
- A queued GenieACS task is never treated as successful application:
  configuration success requires read-back verification where technically
  possible.
- Raw CPE secrets are never stored or logged — only encrypted references and
  masked metadata.
- Firmware is validated (checksum), approved, and deployed through canary /
  phased rollouts; rollback is claimed only for devices that support it.

## API

See [`docs/apis/milestone-7-device-management.md`](../../docs/apis/milestone-7-device-management.md).
