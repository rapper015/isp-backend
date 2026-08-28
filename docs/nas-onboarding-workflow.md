# NAS Onboarding, Desired-State and Change Plans

## Secure onboarding workflow

1. Administrator creates a NAS draft (`POST /api/nas`). The backend validates
   tenant, permissions, management address/port, duplicate management address and
   duplicate RADIUS source IP, then stores RouterOS credentials **encrypted**.
2. `POST /api/nas/{id}/test-connection` queues a connection-test job. The worker
   connects to RouterOS, retrieves identity/version and detects capabilities.
   Failures map to stable codes (`AUTHENTICATION_FAILED`, `CONNECTION_TIMEOUT`,
   `TLS_FAILURE`, `INSUFFICIENT_PERMISSION`, `UNSUPPORTED_ROUTEROS_VERSION`, ...).
3. `POST /api/nas/{id}/discover` queues a discovery job. The worker reads the
   existing RADIUS-related settings, detects capabilities and stores a redacted,
   checksummed snapshot plus `NasRemoteObject` ownership records.
4. The administrator selects primary and secondary RADIUS servers
   (`POST /api/nas/{id}/radius-assignments`). Assignment-specific shared secrets
   are generated and stored encrypted.
5. `POST /api/nas/{id}/desired-configuration` records the desired state
   (services, PPP AAA, hotspot profiles, incoming CoA, interim interval, optional
   router login RADIUS).
6. `POST /api/nas/{id}/plan` builds a **pure, secret-free preview**: add/update/
   remove/noop operations, warnings, risk level and validity.
7. Critical changes require elevated permission and explicit approval
   (`POST /api/nas/{id}/plans/{plan_id}/approve`).
8. `POST /api/nas/{id}/plans/{plan_id}/apply` queues a configuration job. The
   worker re-reads state, recomputes the plan, acquires the per-NAS lock, applies
   additions before removals, preserves the working primary entry until the new
   entry is verified, then re-reads and verifies.
9. `POST /api/nas/{id}/verify` compares desired vs applied state; a redacted
   snapshot is stored and remote object IDs tracked.
10. Registration packages are generated for manual FreeRADIUS work; the NAS waits
    for manual confirmation and a functional verification signal before becoming
    ACTIVE.

Every step is resumable and idempotent (idempotency keys on all commands).

## Connection validation

Before a NAS is marked connected the backend validates:

* management address format + SSRF policy
* port range
* tenant ownership
* duplicate management address / RADIUS source IP
* credential validity (RouterOS login)
* RouterOS API availability
* router identity and version (v6/v7)
* required permissions
* TLS certificate when API-SSL is used

Distinguished failure codes:

```
DNS_FAILURE, NETWORK_UNREACHABLE, CONNECTION_REFUSED, CONNECTION_TIMEOUT,
TLS_FAILURE, AUTHENTICATION_FAILED, INSUFFICIENT_PERMISSION,
UNSUPPORTED_ROUTEROS_VERSION, UNSUPPORTED_DEVICE, INVALID_RESPONSE
```

## Current-state discovery

The worker reads and normalizes: `/radius` entries (address, services, ports,
timeout, src-address), RADIUS incoming/CoA, PPP AAA, router-user AAA, Hotspot
profiles, active sessions, and router identity/version. Discovered objects are
classified as `BACKEND_MANAGED`, `EXTERNALLY_MANAGED`, `UNKNOWN` or
`CONFLICTING`. **Externally managed configuration is never deleted
automatically.**

## Desired-state engine

`app/nas_desired_state.py` is a pure, deterministic engine:

* **Input:** normalized current state, NAS capabilities, selected RADIUS
  servers/services, tenant policy, desired accounting/CoA settings.
* **Output:** noop/add/update/remove operations, warnings, blocking validation
  errors, risk level.
* **Guarantees:** idempotent, stable ordering, versioned, secret-free, safe diff,
  no mutation during preview, independently unit-tested.

## Change plans and approval

A plan preview contains existing values, desired safe values, additions, updates,
removals, warnings, risk level, potential loss-of-access warning and required
manual FreeRADIUS work.

Risk levels:

| Risk | Example |
| --- | --- |
| `low` | adding a secondary RADIUS entry |
| `medium` | changing an accounting interval |
| `high` | replacing the primary RADIUS server |
| `critical` | enabling RADIUS for router administrative login |

Critical changes require elevated permission and explicit approval.

## Apply behavior

1. Acquire per-NAS Redis lock (database fallback).
2. Verify job idempotency.
3. Re-read current RouterOS state.
4. Reject an expired change plan.
5. Detect configuration drift and recompute the plan if required.
6. Capture a redacted snapshot.
7. Apply additions before removals; preserve the working primary entry until the
   new entry is verified where possible.
8. Apply only backend-managed changes; external entries untouched.
9. Read the configuration again and compare desired vs actual.
10. Store remote object IDs.
11. Release the lock and publish result events.

Never execute concurrent configuration jobs against the same NAS.

## Rollback

Rollback restores only the relevant backend-managed RADIUS/AAA settings from the
previous safe snapshot — never an entire router image. Automatic rollback after
failed verification (where safe), manual rollback, rollback preview, audit,
rollback-failure state and operator instructions when automatic rollback is
unsafe are supported (`POST /api/nas/{id}/rollback`).

## Drift detection

`POST /api/nas/{id}/detect-drift` compares desired vs live state and detects:
missing RADIUS entry, changed server address/port/services/timeout/source
address, changed PPP AAA, changed user AAA, changed Hotspot RADIUS, incoming CoA
disabled, remote object removed, unknown external entry added.

Classification: `NONE`, `SAFE`, `WARNING`, `CRITICAL`, `UNKNOWN`. Externally
managed changes are reported but never automatically overwritten unless tenant
policy explicitly enables reconciliation (`reconcile_external_enabled`).

## Health checks

Scheduled health checks cover API connectivity, authentication validity, router
identity, RouterOS version, RADIUS assignment presence, PPP AAA, Hotspot RADIUS,
incoming CoA, drift, and last auth/accounting/CoA activity. Intervals are
configurable (`AAA_NAS_HEALTH_INTERVAL_SECONDS`); routers are not overloaded with
polling.
