# Device Management Security Checklist — Milestone 7

How the device-management service enforces security, and what operators must
configure in production.

## 1. Authentication

- [x] All `/api/device-management/*` routes require a management JWT
  (`DEVICE_MANAGEMENT_JWT_SECRET`) or the internal-service key
  (`X-Internal-API-Key`) for event ingestion.
- [x] JWT secret must be ≥ 32 chars; shorter secrets return HTTP 503 at the
  auth gate.
- [x] Inbound event consumers authenticate with `X-Internal-API-Key` using a
  constant-time compare.
- [ ] **Operator action**: rotate `DEVICE_MANAGEMENT_JWT_SECRET` and the
  internal key; never commit them.

## 2. Authorization / RBAC

- [x] Every endpoint maps to a permission (`device.*`); the authenticated
  role's permissions are enforced server-side.
- [x] Elevated permissions guard destructive/bulk operations:
  - `device.factory_reset` — only DEVICE_OPERATOR, ISP_ADMIN, ISP_OWNER,
    PLATFORM_ADMIN, TENANT_ADMIN
  - `device.firmware.execute` — only FIRMWARE_OPERATOR/APPROVER, ISP_ADMIN,
    PLATFORM_ADMIN
  - `device.transfer` / `device.decommission` / `device.bulk_action`
- [x] `super_admin` and `PLATFORM_ADMIN`/`ISP_OWNER`/`ISP_ADMIN` carry `*`.
- [ ] **Operator action**: review the role matrix in `app/security.py` before
  granting custom roles.

## 3. Tenant isolation

- [x] `tenant_id` is validated against the authenticated JWT principal; a
  mismatch returns 403.
- [x] Devices are looked up tenant-scoped (`get_device_or_404`); cross-tenant
  access to a device id returns 404 (not a data leak).
- [x] Cross-tenant claims are blocked; ownership changes require an explicit,
  reasoned transfer.
- [x] All events/audit rows carry the tenant scope.

## 4. Secret handling

- [x] Wi-Fi/PPPoE/CWMP/connection-request secrets are stored only as encrypted
  references (`secret_ref`), never plaintext.
- [x] Secrets are masked in logs (`mask_secret`) and redacted from log lines
  (`redact_log_line`).
- [x] Sensitive parameters are never returned by APIs and are exempt from
  read-back verification (unreadable by design).
- [x] ACS instance credentials are encrypted in storage.
- [ ] **Operator action**: back the cipher with a real KMS/vault in production
  (the default derives a key from `DEVICE_MANAGEMENT_ENCRYPTION_KEY`).

## 5. SSRF protection

- [x] Connection-request URLs are validated (scheme, port, IP class, DNS);
  link-local/metadata/lan targets are blocked
  (`169.254.169.254`, `10/8`, `172.16/12`, `192.168/16`, `127/8`, `0.0.0.0`,
  `::1`).
- [x] The GenieACS NBI is never exposed to frontends; only the internal
  adapter calls it.

## 6. ACS integration

- [x] The service never touches GenieACS MongoDB.
- [x] All GenieACS HTTP calls go through one adapter with TLS verification,
  timeouts, retries and a circuit breaker.
- [x] The CWMP protocol server is **not** re-implemented here.

## 7. Firmware safety

- [x] Checksum validated at upload; duplicates rejected.
- [x] No rollout of unapproved artifacts.
- [x] Canary-first staging; never full-fleet as stage 1.
- [x] Per-device read-back verification before success.
- [x] Rollback claimed only for hardware that supports it.
- [ ] **Operator action**: store firmware binaries in private storage
  (`DEVICE_FIRMWARE_DIR`), never in a public bucket.

## 8. Configuration safety

- [x] Queued task ≠ success: jobs require read-back verification.
- [x] Offline devices wait for Inform (with timeout), they are not silently
  marked done.
- [x] Unsupported parameters block job creation.
- [x] Drift is detected, classified (`USER_CHANGE`, `SECURITY_CRITICAL`, …) and
  reported, with remediation policies (`REPORT_ONLY`, `AUTO_REMEDIATE`).

## 9. Auditability

- [x] Immutable per-device timeline (`CpeEvent`, aggregate-versioned).
- [x] Ownership history preserved through claim/transfer.
- [x] Global audit log for control-plane actions (device, profile, firmware,
  rollout, ACS).
- [x] Outbox publishes business events (`cpe.*`) with correlation ids.

## 10. Transport / deployment

- [x] Rate limiting on management auth (default 120 req/min per client+path).
- [ ] **Operator action**: terminate TLS at the gateway; keep
  device-management-service and GenieACS on the internal network.
- [ ] **Operator action**: run `device-management-worker` as its own container
  with the same DB credentials and network isolation.
