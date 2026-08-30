# Milestone 0 — AAA Service: NAS & RADIUS API

Service: `aaa-service`. Base docs: `/internal/docs`. Auth: `X-AAA-Service-Key`
(internal service key) unless noted.

## Health / status

| Method | Path | Description |
| --- | --- | --- |
| GET | `/health` | Liveness probe |
| GET | `/status` | Service phase/status |

## Application users & login (operator auth)

This is the **application/user authentication** for the people who use the
system (admins, NOC, sales, KYC, etc.) — **not** RADIUS subscriber auth (see
the Internal RADIUS section below). Login returns a JWT that the management
surfaces of every service verify, so one login token can call the whole
platform when all services share the same `<SVC>_JWT_SECRET`.

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/aaa/users` | Create an application user (`X-AAA-Service-Key` required) |
| GET | `/api/aaa/users` | List application users (`X-AAA-Service-Key` required) |
| POST | `/api/aaa/login` | Login with username + password → `{access_token, ...}` |
| POST | `/admin-login` | Alias for login (the gateway maps `/api/v1/auth/login` here) |
| GET | `/api/aaa/auth/me` | Current user from the Bearer token |

**Create user** — `POST /api/aaa/users` (internal service key):
```json
{ "username": "alice", "password": "Str0ng!Passw0rd", "full_name": "Alice",
  "email": "alice@isp.example", "role": "PLATFORM_ADMIN", "tenant_id": null }
```
Roles are free-form strings; `PLATFORM_ADMIN`/`ISP_OWNER`/`ISP_ADMIN` map to `*`
permission on every service, `READ_ONLY` is the safe default.

**Login** — `POST /api/aaa/login`:
```json
{ "username": "alice", "password": "Str0ng!Passw0rd" }
```
Returns `access_token` (HS256 JWT, default 12h TTL via `AAA_TOKEN_TTL_SECONDS`),
`token_type`, `expires_in`, and the `user` object. Use the token as
`Authorization: Bearer <token>` on any `/api/*` management route.

**Bootstrap first admin** — if no users exist, the service creates one at
startup from env:
```
AAA_BOOTSTRAP_ADMIN_USERNAME=admin
AAA_BOOTSTRAP_ADMIN_PASSWORD=<strong-password>
```
Signing secret: `PLATFORM_JWT_SECRET` if set, else `AAA_JWT_SECRET` (must be
≥ 32 chars). To let one token hit every service, set all `*_JWT_SECRET` envs to
the same value.

## Internal RADIUS (used by the RADIUS server / FreeRADIUS integration)

| Method | Path | Description |
| --- | --- | --- |
| GET | `/internal/radius/v1/health` | RADIUS worker health |
| GET | `/internal/radius/v1/readiness` | RADIUS worker readiness |
| GET | `/internal/radius/v1/metrics` | RADIUS metrics snapshot |
| POST | `/internal/radius/v1/authenticate` | Authenticate a subscriber (Access-Request) |
| POST | `/internal/radius/v1/authorize` | Authorize + build Access-Accept reply attributes |
| POST | `/internal/radius/v1/accounting` | Ingest accounting Start/Interim/Stop |
| POST | `/internal/radius/v1/post-auth` | Post-authentication hooks |

## Tenants

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/aaa/tenants` | Create a tenant (with default policy) |

## NAS devices (canonical management API)

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/nas` | Register a NAS device |
| GET | `/api/nas` | List NAS devices (tenant-scoped) |
| GET | `/api/nas/{nas_id}` | NAS detail |
| PATCH | `/api/nas/{nas_id}` | Update NAS management fields |
| DELETE | `/api/nas/{nas_id}` | Delete NAS |
| POST | `/api/nas/{nas_id}/enable` | Enable NAS |
| POST | `/api/nas/{nas_id}/disable` | Disable NAS |
| POST | `/api/nas/{nas_id}/decommission` | Decommission NAS (lifecycle) |
| POST | `/api/nas/{nas_id}/credentials` | Store encrypted management credentials |
| POST | `/api/nas/{nas_id}/credentials/rotate` | Rotate management credentials |
| POST | `/api/nas/{nas_id}/test-connection` | Test RouterOS connection |
| GET | `/api/nas/{nas_id}/connection-status` | Last connection status |
| POST | `/api/nas/{nas_id}/discover` | Discover router identity/version/capabilities |
| GET | `/api/nas/{nas_id}/capabilities` | Cached capability flags |
| GET | `/api/nas/{nas_id}/current-radius-configuration` | Read live RADIUS config |
| GET | `/api/nas/{nas_id}/snapshots` | Configuration snapshots |

## NAS ↔ RADIUS assignments & secret rotation

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/nas/{nas_id}/radius-assignments` | Create a RADIUS assignment |
| GET | `/api/nas/{nas_id}/radius-assignments` | List assignments |
| PATCH | `/api/nas/{nas_id}/radius-assignments/{assignment_id}` | Update assignment |
| DELETE | `/api/nas/{nas_id}/radius-assignments/{assignment_id}` | Remove assignment |
| POST | `/api/nas/{nas_id}/radius-assignments/{assignment_id}/registration-package` | Generate registration package |
| POST | `/api/nas/{nas_id}/radius-assignments/{assignment_id}/registration-package/reveal` | Reveal package (one-time, audited) |
| POST | `/api/nas/{nas_id}/radius-assignments/{assignment_id}/confirm-registration` | Confirm manual registration |
| POST | `/api/nas/{nas_id}/radius-assignments/{assignment_id}/verify` | Verify assignment on router |
| GET | `/api/nas/{nas_id}/radius-registration-status` | Registration status summary |
| POST | `/api/nas/{nas_id}/radius-assignments/{assignment_id}/rotate-secret` | Start RADIUS secret rotation |
| POST | `/api/nas/{nas_id}/radius-assignments/{assignment_id}/confirm-freeradius-update` | Confirm FreeRADIUS updated |
| POST | `/api/nas/{nas_id}/radius-assignments/{assignment_id}/apply-secret` | Apply new secret to router |
| POST | `/api/nas/{nas_id}/radius-assignments/{assignment_id}/rollback-secret` | Roll back secret rotation |

## NAS desired-state / change plans / jobs

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/nas/{nas_id}/desired-configuration` | Create/update desired configuration |
| GET | `/api/nas/{nas_id}/desired-configuration` | Read desired configuration |
| POST | `/api/nas/{nas_id}/plan` | Build a change plan (diff, risk, approval) |
| GET | `/api/nas/{nas_id}/plans/{plan_id}` | Plan detail |
| POST | `/api/nas/{nas_id}/plans/{plan_id}/approve` | Approve plan |
| POST | `/api/nas/{nas_id}/plans/{plan_id}/apply` | Apply plan (idempotent) |
| GET | `/api/nas/{nas_id}/jobs` | List configuration jobs |
| GET | `/api/nas/{nas_id}/jobs/{job_id}` | Job detail |
| POST | `/api/nas/{nas_id}/jobs/{job_id}/cancel` | Cancel a queued job |
| POST | `/api/nas/{nas_id}/rollback` | Roll back to last verified configuration |
| POST | `/api/nas/{nas_id}/verify` | Verify applied configuration on router |
| POST | `/api/nas/{nas_id}/detect-drift` | Detect configuration drift |
| POST | `/api/nas/{nas_id}/reconcile` | Reconcile desired vs actual |
| GET | `/api/nas/{nas_id}/health` | NAS health |
| GET | `/api/nas/{nas_id}/activity` | Recent orchestration activity |
| GET | `/api/nas/{nas_id}/audit` | NAS audit log |

## Credentials (subscriber)

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/aaa/credentials` | Create a subscriber credential |
| PATCH | `/api/aaa/credentials/{credential_id}` | Update credential |
| POST | `/api/aaa/credentials/{credential_id}/revoke` | Revoke credential |

## Sessions

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/aaa/sessions` | List active sessions (tenant-scoped) |
| GET | `/api/aaa/sessions/{session_id}` | Session detail |
| POST | `/api/aaa/sessions/reconcile` | Plan session reconciliation vs router (simulation) |
| GET | `/api/aaa/accounting-events` | List accounting events |
| POST | `/api/aaa/accounting-events/{event_id}/replay` | Replay an accounting event |

## CoA / Disconnect

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/aaa/nas/{nas_id}/test-coa` | Test CoA for a session |
| POST | `/api/aaa/sessions/{session_id}/disconnect` | Disconnect one session (idempotent) |
| POST | `/api/aaa/subscribers/{subscriber_id}/disconnect` | Disconnect all subscriber sessions |
| POST | `/api/aaa/sessions/{session_id}/coa` | Send CoA to a session |
| POST | `/api/aaa/subscribers/{subscriber_id}/coa` | Send CoA to all subscriber sessions |

## IP pools / leases

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/aaa/ip-pools` | Create IP pool |
| GET | `/api/aaa/ip-pools` | List IP pools |
| GET | `/api/aaa/ip-pools/{pool_id}/leases` | List pool leases |
| POST | `/api/aaa/ip-pools/{pool_id}/reservations` | Reserve an address |
| POST | `/api/aaa/ip-leases/{lease_id}/release` | Release a lease |

## RADIUS servers & groups

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/aaa/radius-servers` | Create RADIUS server |
| GET | `/api/aaa/radius-servers` | List servers |
| GET | `/api/aaa/radius-servers/{server_id}` | Server detail |
| PATCH | `/api/aaa/radius-servers/{server_id}` | Update server |
| DELETE | `/api/aaa/radius-servers/{server_id}` | Delete server |
| POST | `/api/aaa/radius-servers/{server_id}/enable` | Enable server |
| POST | `/api/aaa/radius-servers/{server_id}/disable` | Disable server |
| POST | `/api/aaa/radius-servers/{server_id}/heartbeat` | Record heartbeat |
| POST | `/api/aaa/radius-server-groups` | Create server group |
| GET | `/api/aaa/radius-server-groups` | List groups |
| PATCH | `/api/aaa/radius-server-groups/{group_id}` | Update group |
| DELETE | `/api/aaa/radius-server-groups/{group_id}` | Delete group |

## Subscriber policy & eligibility

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/aaa/subscribers/{subscriber_id}/effective-policy` | Effective policy (simulation) |
| POST | `/api/aaa/subscribers/{subscriber_id}/preview-policy` | Preview policy with overrides |
| POST | `/api/aaa/subscribers/{subscriber_id}/test-eligibility` | Eligibility test (decision) |
| POST | `/api/aaa/subscribers/{subscriber_id}/rotate-credential` | Rotate password |
| POST | `/api/aaa/subscribers/{subscriber_id}/enable` | Enable/disable account |

## Usage & audit

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/aaa/usage` | List usage projections |
| GET | `/api/aaa/usage/subscribers/{subscriber_id}` | Subscriber usage |
| POST | `/api/aaa/usage/subscribers/{subscriber_id}/reset` | Reset quota/FUP for a period |
| GET | `/api/aaa/audit` | Audit log (tenant-scoped, filterable) |

## Legacy alias routes (temporary compatibility)

`/api/aaa/nas*` mirrors `/api/nas*` (same semantics). See the canonical routes
above for details.
