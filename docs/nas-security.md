# NAS Management — Security Considerations

## Secret management

* RouterOS credentials are stored in `NasCredential` as Fernet ciphertext with a
  key version; they are never returned by any API.
* RADIUS shared secrets are stored per assignment in `NasRadiusAssignment` as
  Fernet ciphertext; primary and secondary assignments can use different secrets.
* Shared secrets are visible only through the privileged one-time reveal
  workflow; every access is audited and tokens expire.
* Plaintext secrets never appear in logs, RabbitMQ payloads, Redis, audit rows,
  snapshots or list APIs. `sanitize_configuration()` strips secret-shaped keys
  before any desired state or snapshot is persisted, and `redact()` is applied to
  router reads.
* Key rotation is staged: the previous encrypted secret is retained for a short
  rollback window and then archived/cleared per policy.

## SSRF and network policy

* Management addresses must be concrete unicast IPs inside
  `NAS_APPROVED_NETWORKS`. Loopback, link-local (e.g. `169.254.169.254`),
  multicast, unspecified, broadcast and reserved addresses are always rejected.
* Hostnames require explicit opt-in (`NAS_ALLOW_HOSTNAMES=true`) and reserved
  suffixes (`.internal`, `.local`, `.localhost`) are rejected.
* Connection and command timeouts are enforced; failed connections cannot scan
  arbitrary hosts.

## Tenant isolation and RBAC

* Every NAS lookup is tenant-scoped through the same queried helper.
* Management JWT claims carry role + permissions; routes map to granular
  permissions (`nas.view` ... `nas.radius_secret.view_once`). Tenant mismatch is
  rejected.
* Critical actions (router login RADIUS, decommission, secret rotation) require
  elevated permissions; `super_admin` can manage all tenants.

## Concurrency safety

* Per-NAS distributed locks (Redis `SET NX EX`) with a database fallback
  (`nas_operation_locks`) ensure two jobs can never configure the same NAS
  simultaneously, even when Redis is unavailable.
* Job idempotency keys prevent duplicate router entries on RabbitMQ retries.
* A per-NAS circuit breaker trips after repeated failures and allows a single
  half-open probe, preventing hammering of unhealthy routers.

## Non-goals / guardrails

The integration is deliberately narrow and must not be used to:

* scan arbitrary hosts or reach cloud metadata endpoints
* access loopback or internal services without policy
* execute arbitrary RouterOS commands
* change unrelated router configuration (firewall, routing, VLANs, interfaces,
  PPP/Hotspot users, queues)
* upgrade firmware or reboot the router
* exfiltrate RouterOS credentials or reveal shared secrets repeatedly

Only the NAS settings necessary for the AAA/RADIUS integration are managed, and
only when explicitly requested and approved.
