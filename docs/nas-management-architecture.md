# NAS / MikroTik RouterOS Management — Architecture

This document describes the backend that lets ISP administrators onboard,
configure, monitor and manage MikroTik NAS routers. It is implemented entirely
inside the **AAA service** (`services/aaa-service`), which already owns the
tenants, subscribers, RADIUS servers/groups, sessions, CoA/Disconnect adapters,
audit infrastructure, RabbitMQ outbox and Redis/Valkey helpers that this
feature reuses.

> **Boundary:** FreeRADIUS is hosted and configured manually outside this
> service. The backend **never** installs, deploys, reconfigures, restarts or
> SSHes into FreeRADIUS, and never writes to its SQL database. It only
> generates the manual registration details and tracks confirmation and
> technical verification.

## Logical architecture

```text
Frontend
  -> Python backend NAS APIs (/api/nas/...)
  -> desired NAS configuration
  -> configuration job (NasJob)
  -> RabbitMQ (nas.jobs queue / aaa.events.v1 exchange)
  -> RouterOS worker (worker_runner -> process_nas_job)
  -> MikroTik RouterOS API (RouterOSAdapter)
  -> verification (read-back + desired vs applied diff)
  -> configuration snapshot (redacted)
  -> final status (lifecycle, connection, configuration)
```

RADIUS relationship:

```text
Backend RadiusServer records
  -> NAS-to-RADIUS assignments (NasRadiusAssignment, per-assignment secret)
  -> RouterOS configuration (/radius entries, PPP/Hotspot AAA, incoming)
  -> MikroTik sends RADIUS traffic to the manually hosted FreeRADIUS
```

FreeRADIUS registration stays separate:

```text
Backend generates registration package (one-time secret reveal)
  -> administrator configures FreeRADIUS manually
  -> administrator confirms (tracked, never auto-verified)
  -> backend records a functional verification signal when available
```

## Modules (services/aaa-service/app)

| Module | Responsibility |
| --- | --- |
| `models.py` | NAS domain persistence (see below) |
| `routeros.py` | Replaceable RouterOS adapter interface, real + fake adapters, normalization, SSRF-safe validation |
| `nas_lifecycle.py` | Validated state machines: lifecycle, job, registration, secret rotation |
| `nas_desired_state.py` | Deterministic desired-state engine, diff, risk, ownership classification |
| `nas_planning.py` | Secret-free planning wrapper, configuration hashing |
| `nas_drift.py` | Drift detection and classification |
| `nas_service.py` | Orchestration: connection test, discovery, apply, verify, rollback, health |
| `nas_registration.py` | Manual FreeRADIUS registration tracking + one-time packages |
| `nas_rotation.py` | Staged shared-secret rotation |
| `locks.py` | Per-NAS distributed locks (Redis + database fallback) |
| `circuit_breaker.py` | Per-NAS circuit breaker |
| `events.py` | RabbitMQ topology, outbox publishing, NAS job consumer |
| `workers.py` | NAS job processor + scheduled health checks |
| `worker_runner.py` | Worker loop |
| `main.py` | FastAPI routes under `/api/nas/...` and `/api/aaa/...` |
| `security.py` | Permissions, JWT/internal auth, Fernet encryption |

## Domain models

* `Nas` — canonical NAS device. Management address (`management_host`,
  `management_port`, `management_protocol`, `api_mode`, `tls_verify`) is kept
  **separate** from the RADIUS source IP (`source_ip`, `radius_source_ipv6`).
  Tracks lifecycle, connection, configuration, registration and health status,
  plus timestamps for connected/discovered/configured/verified/auth/accounting/CoA.
* `NasCredential` — encrypted RouterOS username/password with key version, API
  port, TLS settings and certificate reference. **Never** returned through APIs.
* `NasCapability` — versioned, normalized capability flags detected at discovery.
* `NasRadiusAssignment` — NAS ↔ logical RADIUS server relationship with priority,
  role, services, per-assignment ports/timeout/source address and an
  **assignment-specific encrypted shared secret** (primary and secondary may use
  different secrets).
* `NasDesiredConfiguration` — versioned, secret-free desired state.
* `NasConfigurationSnapshot` — redacted, checksummed snapshots of the managed
  RouterOS scope; never full unredacted exports.
* `NasChangePlan` — immutable preview with operations, risk and validation.
* `NasConfigurationJob` — durable job with idempotency key, attempts, result.
* `NasRemoteObject` — observed RouterOS objects with ownership classification
  (`BACKEND_MANAGED`, `EXTERNALLY_MANAGED`, `UNKNOWN`, `CONFLICTING`). The backend
  never relies on unstable RouterOS row ordering.
* `NasHealthCheck` — append-only health check results with sanitized diagnostics.
* `NasSecretRotation` — staged secret rotation state.
* `NasSecretReveal` — one-time reveal tokens (registration and rotation).
* `NasOperationLock` — database fallback for per-NAS locks.

## State machines

All transitions are validated; callers cannot set states arbitrarily. See
`app/nas_lifecycle.py`.

**NAS lifecycle:** `DRAFT -> CONNECTION_PENDING -> CONNECTION_TESTING -> CONNECTED
-> DISCOVERING -> DISCOVERED -> (RADIUS_REGISTRATION_PENDING -> ... ) ->
CONFIGURATION_PENDING -> CONFIGURATION_PLANNED -> AWAITING_APPROVAL -> CONFIGURING
-> VERIFYING -> CONFIGURED -> TESTING -> ACTIVE`, with `DEGRADED`, `FAILED`,
`DISABLED`, `DECOMMISSIONING -> DECOMMISSIONED` branches.

**Configuration job:** `PENDING -> QUEUED -> RUNNING -> VERIFYING -> SUCCEEDED`
(or `FAILED`), with `ROLLBACK_PENDING -> ROLLING_BACK -> ROLLED_BACK` /
`ROLLBACK_FAILED` and `CANCELLED`.

**FreeRADIUS registration:** `PENDING -> DETAILS_GENERATED ->
AWAITING_MANUAL_CONFIGURATION -> MANUALLY_CONFIRMED -> VERIFICATION_PENDING ->
VERIFIED`, with `VERIFICATION_FAILED`, `SECRET_ROTATION_PENDING`, `NOT_REQUIRED`,
`DISABLED`. Manual confirmation never implies verified; verification requires a
functional signal.

**Secret rotation:** `ROTATION_DRAFT -> NEW_SECRET_GENERATED ->
AWAITING_FREERADIUS_UPDATE -> FREERADIUS_UPDATE_CONFIRMED -> ROUTER_UPDATE_PENDING
-> ROUTER_UPDATED -> VERIFYING -> ACTIVE`, with `ROLLBACK_PENDING -> ROLLED_BACK`
and `FAILED`.

## Security posture

* RouterOS passwords and RADIUS shared secrets are encrypted with Fernet and key
  versioning (`security.encrypt_secret`). They never appear in API responses,
  logs, audits, RabbitMQ payloads or Redis.
* SSRF protection: management addresses must be concrete unicast IPs inside
  approved networks; loopback/link-local/metadata/reserved addresses are always
  rejected (`routeros.validate_management_address`).
* Per-NAS Redis locks with a database fallback prevent concurrent configuration
  jobs; Redis unavailability can never cause two workers to configure the same
  NAS.
* Least-privilege RouterOS users are required and validated (see
  `nas-routeros-integration.md`).
* Structured error codes (`CONNECTION_TIMEOUT`, `AUTHENTICATION_FAILED`, ...) are
  the only errors exposed; raw socket/TLS exceptions are never surfaced.
