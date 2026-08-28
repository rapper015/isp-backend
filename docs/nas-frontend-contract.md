# Frontend Integration Contract

All endpoints are served through the gateway under `/api/nas/...` and require
authentication (internal service key or a management JWT), tenant isolation,
granular permission checks, validation, pagination/filtering/sorting, rate
limiting, idempotency keys on commands, and audit history. OpenAPI is available
at `/internal/docs` / `/internal/openapi.json`.

## Authentication

Management calls use a signed JWT with a `role` and `permissions`. The AAA
service maps routes to permissions such as `nas.view`, `nas.create`,
`nas.configuration.apply`, `nas.radius_secret.view_once`, `nas.audit.view`.
`super_admin` bypasses checks; `noc_admin` is granted the NAS management set.

## NAS lifecycle

```
POST   /api/nas
GET    /api/nas
GET    /api/nas/{id}
PATCH  /api/nas/{id}
DELETE /api/nas/{id}
POST   /api/nas/{id}/enable
POST   /api/nas/{id}/disable
POST   /api/nas/{id}/decommission
```

## Credentials and connection

```
POST /api/nas/{id}/credentials
POST /api/nas/{id}/credentials/rotate
POST /api/nas/{id}/test-connection?tenant_id=...&idempotency_key=...&sync=false
GET  /api/nas/{id}/connection-status
```

Stored RouterOS passwords are **never** returned.

## Discovery

```
POST /api/nas/{id}/discover?tenant_id=...&idempotency_key=...&sync=false
GET  /api/nas/{id}/capabilities
GET  /api/nas/{id}/current-radius-configuration
GET  /api/nas/{id}/snapshots
```

## Desired configuration and change plans

```
POST /api/nas/{id}/desired-configuration
GET  /api/nas/{id}/desired-configuration
POST /api/nas/{id}/plan
GET  /api/nas/{id}/plans/{plan_id}
POST /api/nas/{id}/plans/{plan_id}/approve
POST /api/nas/{id}/plans/{plan_id}/apply?sync=false
```

## RADIUS assignments

```
POST   /api/nas/{id}/radius-assignments
GET    /api/nas/{id}/radius-assignments
PATCH  /api/nas/{id}/radius-assignments/{assignment_id}
DELETE /api/nas/{id}/radius-assignments/{assignment_id}
```

## FreeRADIUS registration

```
POST /api/nas/{id}/radius-assignments/{a}/registration-package
POST /api/nas/{id}/radius-assignments/{a}/registration-package/reveal?reveal_token=...
POST /api/nas/{id}/radius-assignments/{a}/confirm-registration
POST /api/nas/{id}/radius-assignments/{a}/verify
GET  /api/nas/{id}/radius-registration-status
```

## Secret rotation

```
POST /api/nas/{id}/radius-assignments/{a}/rotate-secret
POST /api/nas/{id}/radius-assignments/{a}/confirm-freeradius-update?rotation_id=...
POST /api/nas/{id}/radius-assignments/{a}/apply-secret?rotation_id=...
POST /api/nas/{id}/radius-assignments/{a}/rollback-secret?rotation_id=...
```

## Configuration operations, health

```
GET  /api/nas/{id}/jobs
GET  /api/nas/{id}/jobs/{job_id}
POST /api/nas/{id}/jobs/{job_id}/cancel
POST /api/nas/{id}/rollback
POST /api/nas/{id}/verify
POST /api/nas/{id}/detect-drift
POST /api/nas/{id}/reconcile
GET  /api/nas/{id}/health
GET  /api/nas/{id}/activity
GET  /api/nas/{id}/audit
```

## API examples (fake values)

Create a NAS draft:

```bash
curl -X POST http://localhost:4000/api/nas \
  -H "Authorization: Bearer TOKEN" -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "3b0c9f10-...", "name": "Main CCR",
    "management_host": "10.20.0.1", "management_port": 8729,
    "management_protocol": "api_ssl", "routeros_username": "isp-app",
    "routeros_password": "REDACTED", "radius_source_ip": "10.30.0.1",
    "services": ["pppoe"]
  }'
# -> {"id":"18ac889f-...","lifecycle_status":"DRAFT","connection_status":"UNKNOWN","correlation_id":"..."}
```

Test connection (async job):

```bash
curl -X POST "http://localhost:4000/api/nas/18ac889f-.../test-connection?tenant_id=3b0c9f10-...&idempotency_key=conn-1"
# -> {"job_id":"...","status":"QUEUED","duplicate":false,"correlation_id":"..."}
```

Create a primary RADIUS assignment:

```bash
curl -X POST "http://localhost:4000/api/nas/18ac889f-.../radius-assignments?tenant_id=3b0c9f10-..." \
  -H "Authorization: Bearer TOKEN" -H "Content-Type: application/json" \
  -d '{"radius_server_id":"d5c9a2b1-...","role":"primary","services":["pppoe"]}'
# -> {"id":"...","registration_status":"DETAILS_GENERATED","secret_displayed":false,"correlation_id":"..."}
```

Desired configuration, plan, apply:

```bash
curl -X POST ".../desired-configuration?tenant_id=..." -d '{"services":["pppoe"],"ppp_aaa":true,"incoming_coa":true,"interim_update_seconds":600}'
curl -X POST ".../plan?tenant_id=..."
curl -X POST ".../plans/{plan_id}/apply?tenant_id=...&sync=true" -d '{"idempotency_key":"apply-1"}'
```

Reveal a registration secret once:

```bash
curl -X POST ".../radius-assignments/{a}/registration-package?tenant_id=..."
# -> {"reveal_token":"...","expires_in_seconds":600,"correlation_id":"..."}
curl -X POST ".../radius-assignments/{a}/registration-package/reveal?tenant_id=...&reveal_token=..."
# -> { ..., "shared_secret":"...", "display_once":true, "text":"# FreeRADIUS NAS registration ..." }
```

## RabbitMQ

Exchange `aaa.events.v1` (topic, durable) with retry (`aaa.retry.v1`) and
dead-letter (`aaa.dead.v1`) exchanges. Queues include `aaa.accounting`,
`aaa.commands`, `aaa.coa` and **`nas.jobs`** (bound to `nas.#`). Publication uses
publisher confirms; consumption uses explicit acks, retry/backoff queues, and a
consumer inbox for idempotency. The envelope carries `event_id`, `event_type`,
`schema_version`, `tenant_id`, `nas_id`, `correlation_id`, `causation_id`,
`idempotency_key`, `occurred_at`, `published_at`, `producer` and a safe payload.

Versioned NAS events:

```
nas.connection_test.requested|completed|failed.v1
nas.discovery.requested|completed|failed.v1
nas.configuration.plan_created.v1
nas.configuration.requested|started|completed|failed.v1
nas.configuration.rollback_requested|completed|failed.v1
nas.configuration.drift_detected.v1
nas.health_changed.v1
nas.radius_registration.generated|confirmed|verified.v1
nas.radius_secret_rotation.requested|completed|failed.v1
```

RouterOS credentials and RADIUS shared secrets are never published.

## Redis / Valkey keys

Redis is transient state only (never the source of truth):

```
aaa:v1:lock:{nas_id}            per-NAS distributed lock (NX EX TTL)
aaa:v1:cb:{nas_id}:failures     circuit breaker failure count
aaa:v1:cb:{nas_id}:open_until   circuit breaker open window
aaa:v1:cb:{nas_id}:probe        half-open probe guard
aaa:v1:limit:{scope}            rate limits
aaa:v1:{tenant}:{kind}:{id}     short-lived caches / one-time tokens
```

If Redis is unavailable, per-NAS mutual exclusion falls back to the
`nas_operation_locks` table so two jobs can never configure the same NAS.

## Troubleshooting

* Connection failures surface stable codes; check that the management IP is in
  `NAS_APPROVED_NETWORKS`, the RouterOS API/API-SSL service is enabled on the
  right port for the backend source, and the dedicated user has
  `api,read,write`.
* `INSUFFICIENT_PERMISSION` → the RouterOS user policy is too narrow.
* `AUTHENTICATION_FAILED` → rotate the NAS credential
  (`POST /api/nas/{id}/credentials/rotate`).
* Apply stuck QUEUED → check the `aaa-worker` is running and the per-NAS lock is
  not held by a crashed worker (lock TTL expires).
* Verification failures → `POST /api/nas/{id}/detect-drift` and the last job
  result explain the difference; `POST /api/nas/{id}/rollback` restores the
  previous managed snapshot.
* Test locally with `AAA_ROUTEROS_ADAPTER=fake` (tests/simulations only).
