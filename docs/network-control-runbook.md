# Milestone 3 — Advanced Network Control: Operational Runbook

## Service

- Implemented inside **`aaa-service`** (the AAA bounded context already owns
  RADIUS, sessions, NAS/RouterOS). No new service; no duplicate modules.
- Python package: `services/aaa-service/app/network_control/`
  - `policy_engine.py`, `radius_compiler.py`, `fup.py`, `qos.py`,
    `routeros_control.py`, `control_actions.py`, `session_registry.py`,
    `ip_identity.py`, `reconciliation.py`, `router.py`, `enums.py`, `schemas.py`
- API routes mounted at `/api/aaa/*` under `internal_service_auth`
  (`X-AAA-Service-Key`). Existing management JWT also grants scoped M3
  permissions via `ROLE_PERMISSIONS` (`network_operator`, extended `noc_admin`).

## Environment variables

| Variable | Purpose |
| --- | --- |
| `AAA_ROUTEROS_ADAPTER` | `api`/`api_ssl` for real routers, `fake` for tests/simulations |
| `AAA_JWT_SECRET` | management JWT signing (>=32 chars) |
| `AAA_INTERNAL_API_KEY(S)` | internal service key(s), comma separated |
| `AAA_ENCRYPTION_KEY` | Fernet key for NAS credentials / secrets |
| `AAA_TRUSTED_SOURCES` | comma-separated allowed internal client IPs |
| `AAA_MTLS_IDENTITIES` | optional mTLS client identities |
| `AAA_ROUTEROS_CONNECT_TIMEOUT` / `AAA_ROUTEROS_COMMAND_TIMEOUT` | RouterOS timeouts |
| `RABBITMQ_URL`, `VALKEY_URL` | messaging + Redis (both optional, fail-open) |
| `AAA_AUTO_CREATE_SCHEMA` | `true` for dev/tests only; production uses Alembic |

## Daily operations

### Policies
- Create policy → versions are immutable once published; editing an active
  policy creates a new version. Lifecycle:
  `DRAFT → UNDER_REVIEW → APPROVED → SCHEDULED → ACTIVE → SUPERSEDED/DISABLED/ARCHIVED`.
- Explain: `POST /api/aaa/subscribers/{id}/effective-policy/explain` returns the
  full decision (reason code, provenance, radius attributes, winning/rejected rules).

### Sessions
- List: `GET /api/aaa/network/sessions?tenant_id=...`
- Search: `GET /api/aaa/network/sessions/search?username=|ip=|mac=`
- Reapply policy: `POST /api/aaa/network/sessions/{id}/reapply`
- Precise disconnect: `POST /api/aaa/network/sessions/{id}/disconnect`
- Bulk disconnect requires `approved: true` (authorization gate).
- Stale/orphan detection: `POST /api/aaa/network/sessions/classify-stale`,
  `.../detect-orphans`.

### Control actions (CoA / Disconnect)
- Create: `POST /api/aaa/control-actions` (idempotent via `idempotency_key`).
- Outcomes are persisted (`ACK`/`NAK`/`TIMEOUT`) via
  `POST /api/aaa/control-actions/{id}/outcome` (worker/AAA response path).
- Retry/cancel: `POST /api/aaa/control-actions/{id}/retry|cancel`.
- IP/pool changes are flagged `DISCONNECT_AND_REAUTHORIZE` (never a fake CoA).

### RouterOS
- Readiness: `POST /api/aaa/nas/{id}/network-readiness` (non-destructive) →
  status + Winbox guide of missing config.
- Managed config: `POST /api/aaa/nas/{id}/managed-config/read|diff|apply|verify|reconcile`.
  Only objects tagged `managed-by=isp-platform` are platform-owned; manual
  config is never deleted. Diff/reconcile are simulation-only (never auto-apply).

### FUP
- Usage: `GET /api/aaa/fup/subscribers/{id}/usage`
- Reset: `POST /api/aaa/fup/subscribers/{id}/reset`
- Top-up: `POST /api/aaa/fup/subscribers/{id}/topup`

### IP identity
- Search: `GET /api/aaa/ip-identity/search`
- Regulatory lookup: `GET /api/aaa/ip-identity/{ip}/regulatory` — strictly
  audited; requires `aaa.ip.regulatory_lookup` permission.

## Router settings still required through Winbox (per readiness check)

The router must be pre-configured (Winbox/CLI) for full control:

1. Enable API-SSL / REST on the router; create a **least-privilege** user
   (read + write on `/queue`, `/ip/firewall`, `/ppp/active`, `/radius`, `/ip/hotspot`).
2. Add a RADIUS client entry pointing to the AAA service (auth 1812, acct 1813,
   matching shared secret).
3. `/ppp/aaa use-radius=yes accounting=yes` (+ interim update, e.g. 5m).
4. `/radius/incoming accept=yes port=3799` (CoA/Disconnect).
5. Hotspot: enable `use-radius` + `radius-accounting` on profiles (where used).
6. NTP clients for accurate session time.

## Safe management commands

All repair operations are idempotent and audited:

- Re-run readiness / re-read managed config / re-reconcile (see above).
- Retry a failed control action.
- Expire/reset FUP cycle via the FUP reset endpoint.
- Outbox reprocessing: existing `aaa.events.publish_outbox` worker path.
- Dead-letter inspection: existing RabbitMQ dead-letter queues (`*.dead`).

## Observability

- Every policy decision, control action, readiness check and regulatory lookup
  is written to the audit log (`AuditLog`) with tenant + correlation id.
- Outbox events: `policy.*`, `fup.*`, `session.*`, `coa.*`, `router.*`,
  `network.identity_assigned.v1` (see `NETWORK_CONTROL_EVENTS` in `events.py`).
- Redis caches compiled policies only; the database remains authoritative.
- One offline router never fails overall service health; readiness is
  device-specific.

## Tests

- `services/aaa-service` — 164 tests (101 M0 + 63 M3) run without a live router
  (`AAA_ROUTEROS_ADAPTER=fake`). Deterministic fakes are used in tests only;
  production paths use typed operations against the real adapter.
